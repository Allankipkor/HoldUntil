from datetime import datetime, timedelta
import uuid
import re
from sqlalchemy.orm import Session
from backend.app.models import User, UserRole, Deal, DealStatus, ChatLog, PlatformType
from backend.app.services.meta_service import MetaService
from backend.app.services.daraja_service import DarajaService
import logging

logger = logging.getLogger("chat_bot")

# In-memory session tracking mapping phone_or_handle -> session dict
USER_SESSIONS = {}

class ChatBotService:
    @staticmethod
    def get_or_create_user(db: Session, platform: PlatformType, phone_or_handle: str) -> User:
        user = db.query(User).filter(User.phone_or_handle == phone_or_handle).first()
        if not user:
            user = User(
                platform=platform,
                phone_or_handle=phone_or_handle,
                role=UserRole.USER
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    @classmethod
    def process_message(cls, db: Session, platform: PlatformType, phone_or_handle: str, text: str, media_url: str = None) -> str:
        """
        Main entry point for incoming messages.
        Returns the bot's response text.
        """
        user = cls.get_or_create_user(db, platform, phone_or_handle)
        session = USER_SESSIONS.setdefault(phone_or_handle, {"state": "IDLE", "deal_id": None})
        
        normalized_text = text.strip().upper()
        
        # Log incoming message to the database
        active_deal_id = session.get("deal_id")
        
        # If no active deal in session, try to find the user's latest active deal from DB
        if not active_deal_id:
            latest_deal = db.query(Deal).filter(
                (Deal.seller_id == user.id) | (Deal.buyer_id == user.id)
            ).order_by(Deal.created_at.desc()).first()
            if latest_deal and latest_deal.status not in [DealStatus.COMPLETED, DealStatus.REFUNDED, DealStatus.CANCELLED]:
                active_deal_id = latest_deal.id
                session["deal_id"] = active_deal_id
        
        if active_deal_id:
            chat_log = ChatLog(
                deal_id=active_deal_id,
                sender_id=user.id,
                message_content=text,
                media_url=media_url,
                timestamp=datetime.utcnow()
            )
            db.add(chat_log)
            db.commit()

        # Handle Global Commands
        if normalized_text == "HELP":
            return cls._handle_help(platform)
        
        if normalized_text == "SELL":
            session["state"] = "AWAITING_DESC"
            session["deal_id"] = None
            return "Let's set up your secure escrow deal. What is the item description? (e.g. 'HP Pavilion Laptop, 8GB RAM, Used')"

        if normalized_text.startswith("JOIN_"):
            deal_id = text.split("_", 1)[1].strip()
            return cls._handle_join_deal(db, user, deal_id, session)

        if normalized_text in ["CANCEL", "RESET"]:
            session["state"] = "IDLE"
            if active_deal_id:
                deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
                if deal and deal.status in [DealStatus.DRAFT, DealStatus.AWAITING_CONFIRMATION]:
                    deal.status = DealStatus.CANCELLED
                    db.commit()
            session["deal_id"] = None
            return "Dialogue reset. Active draft deal cancelled. Type 'SELL' to start a new transaction."

        if normalized_text.startswith("PRICE"):
            if active_deal_id:
                deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
                if deal and deal.status in [DealStatus.DRAFT, DealStatus.AWAITING_CONFIRMATION, DealStatus.AWAITING_CONFIRMATION]:
                    if deal.seller_id == user.id:
                        price_match = re.search(r"\d+", text)
                        if price_match:
                            new_price = float(price_match.group())
                            deal.agreed_price = new_price
                            deal.seller_confirmed = True
                            deal.buyer_confirmed = False  # buyer must re-confirm
                            db.commit()
                            
                            # Notify buyer if linked
                            if deal.buyer_id:
                                MetaService.send_text_message(
                                    db, platform, deal.buyer.phone_or_handle,
                                    f"⚠️ The seller has updated the deal price to KES {new_price:.2f}.\n\n"
                                    f"Please review the updated details and reply 'CONFIRM' to accept.",
                                    deal.id
                                )
                            return f"Price updated to KES {new_price:.2f}. Awaiting buyer confirmation."
                    else:
                        return "Only the seller can modify the price of this deal."
            return "No active draft deal found to update the price. Type 'SELL' to start a new deal."

        # State-based handling
        state = session["state"]
        if state == "AWAITING_DESC":
            session["draft_desc"] = text
            session["state"] = "AWAITING_PRICE"
            return "Got it. What is the agreed price in Kenyan Shillings (KES)? (Numbers only, e.g. 15000)"

        elif state == "AWAITING_PRICE":
            # Extract numbers
            price_match = re.search(r"\d+", text)
            if not price_match:
                return "Please enter a valid price in numbers (e.g. 15000)."
            price = float(price_match.group())
            session["draft_price"] = price
            session["state"] = "AWAITING_TIMELINE"
            return "How many days should the delivery take? (Enter a number of days, e.g. 3)"

        elif state == "AWAITING_TIMELINE":
            days_match = re.search(r"\d+", text)
            if not days_match:
                return "Please enter the number of days as a number (e.g. 3)."
            days = int(days_match.group())
            
            # Create the deal draft in database
            deal = Deal(
                seller_id=user.id,
                item_description=session["draft_desc"],
                agreed_price=session["draft_price"],
                delivery_deadline=datetime.utcnow() + timedelta(days=days),
                status=DealStatus.DRAFT,
                seller_confirmed=True  # Seller created it, so they auto-confirm
            )
            db.add(deal)
            db.commit()
            db.refresh(deal)
            
            session["deal_id"] = deal.id
            session["state"] = "AWAITING_BUYER_LINK"
            
            invite_text = f"JOIN_{deal.id}"
            invite_link = f"https://wa.me/bot_number?text={invite_text}"
            
            # Return invite instructions to the seller
            response = (
                f"✅ Deal Draft Created!\n\n"
                f"Item: {deal.item_description}\n"
                f"Price: KES {deal.agreed_price:.2f}\n"
                f"Deadline: {days} days\n\n"
                f"Please forward this invite text to your buyer:\n\n"
                f"\"Hey! Let's complete our transaction securely using HoldUntil escrow. "
                f"Tap this link to review and confirm the deal details: {invite_link}\""
            )
            return response

        # Deal confirmation & execution states
        if active_deal_id:
            deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
            if deal:
                # Handle confirmations
                if deal.status == DealStatus.AWAITING_CONFIRMATION:
                    if normalized_text == "CONFIRM":
                        if user.id == deal.seller_id:
                            deal.seller_confirmed = True
                        elif user.id == deal.buyer_id:
                            deal.buyer_confirmed = True
                        db.commit()

                        if deal.seller_confirmed and deal.buyer_confirmed:
                            # Both confirmed! Trigger STK Push to buyer
                            buyer_user = db.query(User).filter(User.id == deal.buyer_id).first()
                            
                            # Update status
                            deal.status = DealStatus.AWAITING_CONFIRMATION
                            db.commit()
                            
                            # Notify seller
                            MetaService.send_text_message(
                                db, platform, deal.seller.phone_or_handle,
                                f"Deal '{deal.item_description[:30]}...' has been confirmed by both parties! "
                                f"We are triggering the payment request to the buyer now.",
                                deal.id
                            )
                            
                            # Trigger STK push
                            try:
                                DarajaService.initiate_stk_push(db, deal, buyer_user.phone_or_handle)
                                return "You have confirmed the deal. We have sent an M-Pesa STK Push to your phone. Please check your phone and enter your M-Pesa PIN to complete the escrow payment."
                            except Exception as e:
                                return f"Escrow setup failed due to M-Pesa connection error. Please try again later. Details: {str(e)}"
                        else:
                            other_party = "Buyer" if user.id == deal.seller_id else "Seller"
                            return f"You confirmed the deal! Waiting for the {other_party} to confirm."

                    elif normalized_text == "REJECT":
                        deal.status = DealStatus.CANCELLED
                        db.commit()
                        
                        # Notify other party
                        other_phone = deal.buyer.phone_or_handle if user.id == deal.seller_id else deal.seller.phone_or_handle
                        if other_phone:
                            MetaService.send_text_message(
                                db, platform, other_phone,
                                f"The deal for '{deal.item_description[:30]}...' was rejected/cancelled by the other party.",
                                deal.id
                            )
                        session["state"] = "IDLE"
                        session["deal_id"] = None
                        return "Deal rejected. The transaction is cancelled."

                # Seller uploads shipping details
                elif deal.status == DealStatus.FUNDED and user.id == deal.seller_id:
                    if normalized_text.startswith("SHIPPED"):
                        # Extract tracking ref
                        parts = text.split(" ", 1)
                        tracking = parts[1].strip() if len(parts) > 1 else ""
                        
                        deal.courier_name = "Courier"  # default
                        deal.tracking_number = tracking
                        deal.status = DealStatus.SHIPPED
                        db.commit()

                        # Prompt Buyer
                        MetaService.send_text_message(
                            db, platform, deal.buyer.phone_or_handle,
                            f"📦 The seller has shipped the item! Tracking number: {tracking}.\n\n"
                            f"Did you receive your item as described? Reply YES or NO",
                            deal.id
                        )
                        return "Got it! We have marked the item as shipped and notified the buyer to confirm receipt."

                    elif media_url:
                        # Photo submitted as shipping evidence
                        # We will process it in a controller route but let's save basic evidence here
                        from backend.app.services.image_service import ImageService
                        
                        # Generate dynamic code to verify
                        if not deal.verification_code:
                            deal.verification_code = f"HU-{uuid.uuid4().hex[:4].upper()}"
                            db.commit()

                        # Return confirmation
                        return (
                            f"Received photo evidence. Processing Image Hash check...\n"
                            f"Please ensure the dynamic code '{deal.verification_code}' is visible in the package photo.\n"
                            f"Type 'SHIPPED <courier_tracking_id>' if you also have a tracking number."
                        )

                # Buyer confirms receipt
                elif deal.status in [DealStatus.SHIPPED, DealStatus.DELIVERY_PROMPTED] and user.id == deal.buyer_id:
                    if normalized_text == "YES":
                        # Release funds to seller!
                        try:
                            # Trigger B2C payout to seller
                            DarajaService.initiate_b2c_payout(db, deal, deal.seller.phone_or_handle, deal.agreed_price, is_refund=False)
                            
                            # Notify seller
                            MetaService.send_text_message(
                                db, platform, deal.seller.phone_or_handle,
                                f"🎉 Buyer confirmed receipt! KES {deal.agreed_price} has been paid out to your M-Pesa account.",
                                deal.id
                            )
                            session["state"] = "IDLE"
                            session["deal_id"] = None
                            return "Awesome! We have released the escrow payment to the seller. Thank you for transacting with HoldUntil."
                        except Exception as e:
                            return f"Failed to process seller payout: {str(e)}. Please contact support."

                    elif normalized_text == "NO":
                        # Initiate dispute
                        deal.status = DealStatus.DISPUTED
                        db.commit()
                        session["state"] = "AWAITING_DISPUTE_REASON"
                        
                        # Notify seller
                        MetaService.send_text_message(
                            db, platform, deal.seller.phone_or_handle,
                            f"⚠️ The buyer has disputed the delivery. HoldUntil escrow funds are locked. We will collect statements from both parties for moderation.",
                            deal.id
                        )
                        return "Escrow dispute registered. Please type your reason for the dispute. What was wrong with the item or delivery?"

                elif session["state"] == "AWAITING_DISPUTE_REASON":
                    # Register the dispute record
                    from backend.app.models import Dispute, DisputeTier
                    
                    dispute = Dispute(
                        deal_id=deal.id,
                        filed_by=user.id,
                        reason=text,
                        tier=DisputeTier.TIER_2_AI
                    )
                    db.add(dispute)
                    db.commit()
                    
                    session["state"] = "IDLE"
                    
                    # Notify both parties
                    MetaService.send_text_message(
                        db, platform, deal.seller.phone_or_handle,
                        f"Dispute details: {text}",
                        deal.id
                    )
                    
                    # Trigger AI resolution check in background or synchronously
                    # Let's import and run AI resolution
                    from backend.app.services.ai_service import AIService
                    try:
                        AIResult = AIService.run_moderation(db, dispute.id)
                        # Depending on AI confidence, it might auto-apply or present to human
                    except Exception as ai_err:
                        logger.error(f"AI moderation trigger error: {ai_err}")

                    return "Thank you. Your dispute statement has been recorded. Our AI Moderator is analyzing the deal transcript and evidence..."

        return f"HoldUntil Escrow Bot. Type 'SELL' to start a new transaction, or 'HELP' for instructions."

    @classmethod
    def _handle_join_deal(cls, db: Session, user: User, deal_id: str, session: dict) -> str:
        deal = db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            return "Invalid invitation link or deal code."
        
        if deal.status != DealStatus.DRAFT:
            return "This deal is no longer open for joining."
        
        if deal.seller_id == user.id:
            return "You are the seller of this deal. You cannot join as the buyer."

        # Link buyer to deal
        deal.buyer_id = user.id
        deal.status = DealStatus.AWAITING_CONFIRMATION
        db.commit()

        session["deal_id"] = deal.id
        session["state"] = "IDLE"
        
        # Set seller session active deal
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        seller_session = USER_SESSIONS.setdefault(seller.phone_or_handle, {"state": "IDLE", "deal_id": None})
        seller_session["deal_id"] = deal.id

        # Send details to buyer
        response = (
            f"🤝 You are joining a secure HoldUntil escrow deal!\n\n"
            f"Seller: {seller.phone_or_handle}\n"
            f"Item: {deal.item_description}\n"
            f"Price: KES {deal.agreed_price:.2f}\n\n"
            f"📜 **Consent & Transparency:**\n"
            f"\"Before funding, note that if a dispute is filed, a HoldUntil moderator "
            f"(automated or human) may review this transaction's chat logs to resolve it fairly. "
            f"Other chats are never accessed. All messages are permanently recorded.\"\n\n"
            f"Reply 'CONFIRM' to accept details & initiate Safaricom M-Pesa STK Push payment.\n"
            f"Reply 'REJECT' to decline."
        )

        # Notify Seller
        MetaService.send_text_message(
            db, PlatformType.WHATSAPP, seller.phone_or_handle,
            f"Buyer {user.phone_or_handle} has joined the deal! Awaiting buyer's confirmation.",
            deal.id
        )

        return response

    @classmethod
    def _handle_help(cls, platform: PlatformType) -> str:
        return (
            f"ℹ️ **HoldUntil Escrow Help**\n\n"
            f"Safaricom M-Pesa payments are locked safely in escrow until the buyer receives and confirms the item.\n\n"
            f"Commands:\n"
            f"• **SELL** — Start setting up a new deal as a seller.\n"
            f"• **CONFIRM** — Confirm transaction terms during setup.\n"
            f"• **REJECT** — Reject and cancel transaction setup.\n"
            f"• **SHIPPED <tracking_number>** — Mark package as sent.\n"
            f"• **YES** — Confirm you received the item (releases cash).\n"
            f"• **NO** — Dispute delivery (locks cash for moderator review)."
        )

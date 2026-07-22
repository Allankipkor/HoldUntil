from datetime import datetime, timedelta, UTC
import uuid
import re
from sqlalchemy.orm import Session
from backend.app.models import User, UserRole, Deal, DealStatus, ChatLog, PlatformType, Dispute, DisputeTier, OutcomeType, ResolutionMethod
from backend.app.services.meta_service import MetaService
from backend.app.services.daraja_service import DarajaService
from backend.app.config import settings
import logging

logger = logging.getLogger("chat_bot")

# In-memory session tracking mapping phone_or_handle -> session dict
USER_SESSIONS = {}
CLOSED_DEAL_SESSIONS = set()

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
    def auto_assign_mediator(cls, db: Session, dispute) -> str:
        """
        Automatically assign a Mediator (MODERATOR role) to the dispute
        using least-open-cases load balancing.
        """
        from backend.app.models import User, UserRole, Dispute
        # Query all mediators
        mediators = db.query(User).filter(User.role == UserRole.MODERATOR).all()
        if not mediators:
            logger.warning("No staff with MODERATOR role found for auto-assignment.")
            # Fallback to any admin
            admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
            if admins:
                mediators = admins
            else:
                return None
        
        # Count open first-instance disputes for each mediator
        mediator_loads = []
        for m in mediators:
            open_count = db.query(Dispute).filter(
                Dispute.human_moderator_id == m.id,
                Dispute.resolved_at == None,
                Dispute.is_appeal == False
            ).count()
            mediator_loads.append((open_count, m.id))
            
        # Select mediator with least open cases
        mediator_loads.sort() # Sorts by count, then mediator id
        assigned_id = mediator_loads[0][1]
        dispute.human_moderator_id = assigned_id
        db.commit()
        logger.info(f"Automatically assigned dispute {dispute.id} to Mediator {assigned_id}")
        return assigned_id

    @classmethod
    def auto_assign_arbitrator(cls, db: Session, dispute) -> str:
        """
        Automatically assign an Arbitrator (ARBITRATOR role) to the appeal dispute
        using least-open-cases load balancing, ensuring they are different from the original mediator.
        """
        from backend.app.models import User, UserRole, Dispute
        # Query all arbitrators
        arbitrators = db.query(User).filter(User.role == UserRole.ARBITRATOR).all()
        # Filter out the first-instance mediator
        valid_arbitrators = [a for a in arbitrators if a.id != dispute.human_moderator_id and a.phone_or_handle != dispute.human_moderator_id]
        
        if not valid_arbitrators:
            logger.warning("No staff with ARBITRATOR role (excluding original mediator) found for auto-appeal-assignment.")
            # Fallback to admins (excluding original mediator)
            admins = db.query(User).filter(User.role == UserRole.ADMIN).all()
            valid_arbitrators = [a for a in admins if a.id != dispute.human_moderator_id and a.phone_or_handle != dispute.human_moderator_id]
            
            if not valid_arbitrators:
                # If still no staff found, just use any arbitrator/admin
                valid_arbitrators = db.query(User).filter(User.role.in_([UserRole.ARBITRATOR, UserRole.ADMIN])).all()
                if not valid_arbitrators:
                    return None
                    
        # Count open appeals for each arbitrator
        arbitrator_loads = []
        for a in valid_arbitrators:
            open_count = db.query(Dispute).filter(
                Dispute.assigned_arbitrator_id == a.id,
                Dispute.resolved_at == None,
                Dispute.is_appeal == True
            ).count()
            arbitrator_loads.append((open_count, a.id))
            
        # Select arbitrator with least open appeals
        arbitrator_loads.sort()
        assigned_id = arbitrator_loads[0][1]
        dispute.assigned_arbitrator_id = assigned_id
        db.commit()
        logger.info(f"Automatically assigned appeal dispute {dispute.id} to Arbitrator {assigned_id}")
        return assigned_id

    @classmethod
    def complete_onboarding(cls, db: Session, platform: PlatformType, phone_or_handle: str, data: dict) -> str:
        """
        Creates a new user in the database upon onboarding completion,
        and resumes their original pending request.
        """
        from backend.app.models import UserRole
        user = User(
            platform=platform,
            phone_or_handle=phone_or_handle,
            name=data.get("name"),
            payout_mpesa_number=data.get("payout_mpesa_number"),
            recovery_email_or_phone=data.get("recovery_contact") or data.get("recovery_email_or_phone"),
            location=data.get("location"),
            consent_accepted_at=datetime.utcnow(),
            role=UserRole.USER
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Onboarded new user: {phone_or_handle} (Name: {user.name})")

        # Get session details and resume pending action
        session = USER_SESSIONS.get(phone_or_handle, {})
        pending_action = session.pop("onboarding_pending_action", None)
        pending_media = session.pop("onboarding_pending_media", None)
        session.pop("onboarding_data", None)
        session["state"] = "IDLE"
        session["deal_id"] = None

        welcome_msg = f"🎉 Welcome to HoldUntil, {user.name}! Your profile has been successfully created.\n\n" \
                      f"💡 *Want faster trust with buyers? Complete 10 deals to unlock your Verified Seller badge.*\n\n"
        
        if pending_action:
            # Re-run dialogue process for the original message
            resumed_reply = cls.process_message(db, platform, phone_or_handle, pending_action, pending_media)
            return welcome_msg + resumed_reply
        else:
            return welcome_msg + "Type 'SELL' to start a new transaction, or 'HELP' for instructions."

    @classmethod
    def process_message(cls, db: Session, platform: PlatformType, phone_or_handle: str, text: str, media_url: str = None) -> str:
        """
        Main entry point for incoming messages.
        Returns the bot's response text.
        """
        # Verify user existence in database
        user = db.query(User).filter(User.phone_or_handle == phone_or_handle).first()
        session = USER_SESSIONS.setdefault(phone_or_handle, {"state": "IDLE", "deal_id": None})
        
        # Check if the incoming message is a simulated JSON Flow submission
        if not user and text.strip().startswith("{") and text.strip().endswith("}"):
            import json
            try:
                flow_data = json.loads(text.strip())
                if "flow_name" in flow_data or "name" in flow_data:
                    return cls.complete_onboarding(
                        db=db,
                        platform=platform,
                        phone_or_handle=phone_or_handle,
                        data={
                            "name": flow_data.get("name"),
                            "payout_mpesa_number": flow_data.get("payout_mpesa_number"),
                            "recovery_contact": flow_data.get("recovery_contact") or flow_data.get("recovery_email_or_phone"),
                            "location": flow_data.get("location")
                        }
                    )
            except Exception as json_err:
                logger.warning(f"Failed to parse text as simulated Flow JSON: {json_err}")

        normalized_text = text.strip().upper()
        
        # Onboarding Flow and text fallback state routing
        if not user:
            state = session.get("state", "IDLE")
            if not state.startswith("ONBOARDING"):
                session["onboarding_pending_action"] = text
                session["onboarding_pending_media"] = media_url
                session["onboarding_data"] = {}
                
                if platform == PlatformType.WHATSAPP:
                    session["state"] = "ONBOARDING_FLOW"
                    return "Welcome to HoldUntil Escrow! To ensure secure transactions and prevent impersonation, " \
                           "we need you to complete a quick, one-time profile setup.\n\n" \
                           "Tap the button below to open the registration Flow.\n\n" \
                           "*(If your WhatsApp version doesn't support Flows, just reply with 'START' to use text-based onboarding instead.)*"
                else:
                    # Messenger or Instagram: go straight to text-based sequence
                    session["state"] = "ONBOARDING_FALLBACK_NAME"
                    return "Welcome to HoldUntil Escrow! To ensure secure transactions and prevent impersonation, " \
                           "we need you to complete a quick, one-time profile setup.\n\n" \
                           "What is your full name?\n\n" \
                           "Helper: We'll quietly check this matches your M-Pesa registration when you transact — this helps protect both buyers and sellers from impersonation."
            
            elif state == "ONBOARDING_FLOW":
                if normalized_text == "START":
                    session["state"] = "ONBOARDING_FALLBACK_NAME"
                    return "Let's proceed with text-based setup. What is your full name?\n\n" \
                           "Helper: We'll quietly check this matches your M-Pesa registration when you transact — this helps protect both buyers and sellers from impersonation."
                else:
                    return "Please complete your profile creation using the WhatsApp Flow button above, or reply 'START' to do it via text messaging instead."
            
            elif state == "ONBOARDING_FALLBACK_NAME":
                if not text.strip():
                    return "Name cannot be empty. Please enter your full name:"
                session["onboarding_data"]["name"] = text.strip()
                session["state"] = "ONBOARDING_FALLBACK_PAYOUT"
                return "Thank you. What is your M-Pesa number to receive payouts?\n\n" \
                       "Helper: Must match the name you entered above — we verify this automatically when you're paid, to protect against impersonation."
            
            elif state == "ONBOARDING_FALLBACK_PAYOUT":
                payout_num = text.strip()
                cleaned_payout = payout_num.replace("+", "")
                if not cleaned_payout.isdigit() or len(cleaned_payout) < 9 or len(cleaned_payout) > 15:
                    return "Invalid phone number format. Please enter a valid M-Pesa payout phone number (e.g., 254711111111):"
                session["onboarding_data"]["payout_mpesa_number"] = payout_num
                session["state"] = "ONBOARDING_FALLBACK_RECOVERY"
                return "Thank you. What is your backup email or phone number?\n\n" \
                       "Helper: This protects you if you ever lose access to WhatsApp, Instagram, or Messenger — you'll still be able to manage your deals."
            
            elif state == "ONBOARDING_FALLBACK_RECOVERY":
                if not text.strip():
                    return "Recovery contact cannot be empty. Please enter a backup email or phone number:"
                session["onboarding_data"]["recovery_contact"] = text.strip()
                session["state"] = "ONBOARDING_FALLBACK_LOCATION"
                return "Got it. What is your general location? (City or town is enough — no full address needed. Reply with your location, or type 'skip' to skip.)\n\n" \
                       "Helper: Helps us match you with nearby local handoff deals."
            
            elif state == "ONBOARDING_FALLBACK_LOCATION":
                loc = text.strip()
                if loc.upper() == "SKIP":
                    session["onboarding_data"]["location"] = None
                else:
                    session["onboarding_data"]["location"] = loc
                session["state"] = "ONBOARDING_FALLBACK_CONSENT"
                return "Finally, please review and agree to the consent terms below:\n\n" \
                       "\"I understand that messages and evidence submitted in HoldUntil transactions may be reviewed to resolve disputes, and that providing false information may result in restricted access.\"\n\n" \
                       "Do you agree? Reply YES or NO."
            
            elif state == "ONBOARDING_FALLBACK_CONSENT":
                if normalized_text in ["YES", "AGREE", "Y"]:
                    return cls.complete_onboarding(
                        db=db,
                        platform=platform,
                        phone_or_handle=phone_or_handle,
                        data={
                            "name": session["onboarding_data"].get("name"),
                            "payout_mpesa_number": session["onboarding_data"].get("payout_mpesa_number"),
                            "recovery_contact": session["onboarding_data"].get("recovery_contact"),
                            "location": session["onboarding_data"].get("location")
                        }
                    )
                elif normalized_text in ["NO", "N"]:
                    return "You must agree to the terms to use HoldUntil. Reply YES when you are ready to accept and complete your profile."
                else:
                    return "Invalid response. Please reply YES or NO to agree to the consent terms."
        
        normalized_text = text.strip().upper()
        
        # Log incoming message to the database
        active_deal_id = session.get("deal_id")
        
        # If the active deal in the session is already marked closed, clear it
        if active_deal_id in CLOSED_DEAL_SESSIONS:
            session["deal_id"] = None
            session["state"] = "IDLE"
            active_deal_id = None

        # If no active deal in session, try to find the user's latest active deal from DB
        if not active_deal_id:
            latest_deal = db.query(Deal).filter(
                (Deal.seller_id == user.id) | (Deal.buyer_id == user.id)
            ).order_by(Deal.created_at.desc()).first()
            if latest_deal and latest_deal.status not in [DealStatus.COMPLETED, DealStatus.REFUNDED, DealStatus.CANCELLED]:
                active_deal_id = latest_deal.id
                session["deal_id"] = active_deal_id
        
        # Guard for no active deal and not in setup/global commands
        is_in_setup = session.get("state") in ["AWAITING_DESC", "AWAITING_PRICE", "AWAITING_TIMELINE", "AWAITING_PAYOUT_UPDATE"]
        is_global_cmd = normalized_text in ["SELL", "HELP", "MY PROFILE", "UPDATE PAYOUT"] or normalized_text.startswith("JOIN_") or normalized_text.startswith("UPDATE PAYOUT ")
        
        if not active_deal_id and not is_in_setup and not is_global_cmd:
            return "You don't have an active deal right now. Message 'SELL' to start one, or share/enter a JOIN code to join someone else's deal."
        
        if active_deal_id:
            chat_log = ChatLog(
                deal_id=active_deal_id,
                sender_id=user.id,
                message_content=text,
                media_url=media_url,
                timestamp=datetime.now(UTC).replace(tzinfo=None)
            )
            db.add(chat_log)
            db.commit()

        # AWAITING_PAYOUT_UPDATE State Handling
        if session.get("state") == "AWAITING_PAYOUT_UPDATE":
            new_payout = text.strip()
            cleaned_payout = new_payout.replace("+", "")
            if not cleaned_payout.isdigit() or len(cleaned_payout) < 9 or len(cleaned_payout) > 15:
                return "Invalid phone number format. Please enter a valid M-Pesa payout phone number (e.g., 254711111111):"
            user.payout_mpesa_number = new_payout
            db.commit()
            session["state"] = "IDLE"
            return f"✅ Payout M-Pesa number successfully updated to: {new_payout}"

        # AWAITING_DISPUTE_RESPONSE State Handling
        if session.get("state") == "AWAITING_DISPUTE_RESPONSE":
            if active_deal_id:
                deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
                if deal:
                    dispute = db.query(Dispute).filter(Dispute.deal_id == deal.id, Dispute.resolved_at == None).first()
                    if dispute:
                        dispute.response_statement = text
                        dispute.response_window_deadline = None  # Clear deadline as they responded
                        db.commit()
                        
                        session["state"] = "IDLE"
                        
                        # Now trigger AI moderation & auto mediator assignment!
                        from backend.app.services.ai_service import AIService
                        try:
                            AIService.run_moderation(db, dispute.id)
                        except Exception as ai_err:
                            logger.error(f"AI moderation trigger error: {ai_err}")
                            
                        cls.auto_assign_mediator(db, dispute)
                        
                        # Notify filer
                        filer_user = db.query(User).filter(User.id == dispute.filed_by).first()
                        if filer_user:
                            MetaService.send_text_message(
                                db, platform, filer_user.phone_or_handle,
                                f"The other party has submitted their response: \"{text}\". The dispute is now under mediator review.",
                                deal.id
                            )
                        
                        return "Thank you. Your response statement has been recorded and submitted for mediator review."

        # Handle Global Commands
        if normalized_text == "HELP":
            return cls._handle_help(platform)
        
        if normalized_text == "MY PROFILE":
            payout_info = user.payout_mpesa_number or "Not Set"
            return (
                f"👤 **Your HoldUntil Profile**\n\n"
                f"• Name: {user.name}\n"
                f"• Recovery: {user.recovery_email_or_phone}\n"
                f"• Location: {user.location or 'Not Set'}\n"
                f"• M-Pesa Payout Number: {payout_info}\n\n"
                f"To update your payout number, reply with 'UPDATE PAYOUT <number>' or 'UPDATE PAYOUT'."
            )

        if normalized_text == "UPDATE PAYOUT" or normalized_text.startswith("UPDATE PAYOUT "):
            parts = text.strip().split(None, 2)
            if len(parts) > 2:
                new_payout = parts[2].strip()
                cleaned_payout = new_payout.replace("+", "")
                if not cleaned_payout.isdigit() or len(cleaned_payout) < 9 or len(cleaned_payout) > 15:
                    return "Invalid phone number format. Please enter a valid M-Pesa payout phone number (e.g., 254711111111):"
                user.payout_mpesa_number = new_payout
                db.commit()
                return f"✅ Payout M-Pesa number successfully updated to: {new_payout}"
            else:
                session["state"] = "AWAITING_PAYOUT_UPDATE"
                return "Please reply with your new M-Pesa payout number:"

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
                    CLOSED_DEAL_SESSIONS.add(deal.id)
            session["deal_id"] = None
            return "Dialogue reset. Active draft deal cancelled. Type 'SELL' to start a new transaction."

        # Post-funding Cancel Confirmation
        if normalized_text == "CONFIRM_CANCEL" and session.get("state") == "CONFIRM_CANCEL_PENDING":
            deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
            if deal and deal.status in [DealStatus.FUNDED, DealStatus.SHIPPED, DealStatus.DELIVERY_PROMPTED]:
                fee = deal.agreed_price * 0.005
                is_buyer_cancel = (user.id == deal.buyer_id)
                
                # Update status immediately
                deal.status = DealStatus.CANCELLED
                db.commit()
                CLOSED_DEAL_SESSIONS.add(deal.id)
                
                if is_buyer_cancel:
                    net_refund = deal.agreed_price - fee
                    DarajaService.initiate_b2c_payout(db, deal, deal.buyer.phone_or_handle, net_refund, is_refund=True)
                    MetaService.send_text_message(db, platform, deal.seller.phone_or_handle, f"⚠️ The buyer has cancelled the deal. The transaction is cancelled.", deal.id)
                    session["deal_id"] = None
                    session["state"] = "IDLE"
                    return f"Deal cancelled. Your refund of KES {net_refund:.2f} (net of 0.5% cancellation fee) is processing."
                else:
                    DarajaService.initiate_b2c_payout(db, deal, deal.buyer.phone_or_handle, deal.agreed_price, is_refund=True)
                    user.trust_score = max(0.0, user.trust_score - 10.0)
                    db.commit()
                    MetaService.send_text_message(db, platform, deal.buyer.phone_or_handle, f"⚠️ The seller has cancelled the deal. A full refund of KES {deal.agreed_price:.2f} is processing.", deal.id)
                    session["deal_id"] = None
                    session["state"] = "IDLE"
                    return f"Deal cancelled. A full refund has been triggered for the buyer. Your Trust Score has been penalized -10.0 points."
            session["state"] = "IDLE"
            return "No active funded deal found to cancel."

        # Appeal Command
        if normalized_text == "APPEAL":
            latest_deal = db.query(Deal).filter(
                (Deal.seller_id == user.id) | (Deal.buyer_id == user.id)
            ).order_by(Deal.created_at.desc()).first()
            if not latest_deal:
                return "No recent transaction found."
            
            dispute = db.query(Dispute).filter(Dispute.deal_id == latest_deal.id).first()
            if not dispute:
                return "No dispute record found for this transaction."
            
            if dispute.resolved_at is None:
                return "You can only appeal a dispute after a first-instance decision has been made."
            
            if dispute.is_appeal:
                return "This dispute has already been appealed. Only one appeal is permitted."
            
            if latest_deal.status != DealStatus.DISPUTED:
                return "The appeal window has expired and funds have already been released."

            # Determine appeal eligibility based on verdict outcome
            is_eligible = False
            lost_party_label = ""
            
            if dispute.final_outcome == OutcomeType.RELEASE:
                # Release Payout fully to Seller -> Buyer lost and can appeal
                is_eligible = (user.id == latest_deal.buyer_id)
                lost_party_label = "Buyer"
            elif dispute.final_outcome == OutcomeType.REFUND:
                # Refund fully to Buyer -> Seller lost and can appeal
                is_eligible = (user.id == latest_deal.seller_id)
                lost_party_label = "Seller"
            elif dispute.final_outcome == OutcomeType.PARTIAL_SPLIT:
                # Partial split -> Either can appeal
                is_eligible = (user.id == latest_deal.buyer_id or user.id == latest_deal.seller_id)
                lost_party_label = "Either party"
                
            if not is_eligible:
                return f"Only the party who lost the dispute ({lost_party_label}) is eligible to appeal this decision."

            # Mark for Senior Arbitrator review
            dispute.is_appeal = True
            dispute.appeal_requested_by = user.id
            dispute.resolved_at = None  # Re-open for human review!
            dispute.tier = DisputeTier.TIER_3_HUMAN
            db.commit()
            
            cls.auto_assign_arbitrator(db, dispute)
            
            # Notify both parties
            appeal_notify_msg = (
                f"⚠️ The dispute for deal '{latest_deal.item_description}' has been appealed by {user.phone_or_handle}. "
                f"It will be reviewed by a Senior Arbitrator. Escrow funds remain locked."
            )
            MetaService.send_text_message(db, platform, latest_deal.buyer.phone_or_handle, appeal_notify_msg, latest_deal.id)
            MetaService.send_text_message(db, platform, latest_deal.seller.phone_or_handle, appeal_notify_msg, latest_deal.id)
            
            session["state"] = "IDLE"
            session["deal_id"] = None
            return (
                f"Dispute appealed successfully. A Senior Arbitrator will audit the case for final review. "
                f"Appeal fee of KES {settings.ESCALATION_FEE_KES:.2f} has been charged. "
                f"This decision will be final within HoldUntil's internal dispute process."
            )

        # Voluntary release/refund commands during disputes
        if active_deal_id:
            deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
            if deal and deal.status == DealStatus.DISPUTED:
                dispute = db.query(Dispute).filter(Dispute.deal_id == deal.id).first()
                if dispute:
                    if normalized_text == "RELEASE" and user.id == deal.buyer_id:
                        # Buyer releases voluntarily
                        dispute.final_outcome = OutcomeType.RELEASE
                        dispute.resolution_method = ResolutionMethod.SELF_RELEASE
                        dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
                        db.commit()
                        
                        DarajaService.initiate_b2c_payout(db, deal, deal.seller.phone_or_handle, deal.agreed_price, is_refund=False)
                        MetaService.send_text_message(db, platform, deal.seller.phone_or_handle, f"🎉 The buyer has voluntarily resolved the dispute and released the funds! Payout is processing.", deal.id)
                        session["deal_id"] = None
                        session["state"] = "IDLE"
                        return "You have voluntarily resolved the dispute and released all funds to the seller. Payout is processing."
                        
                    elif normalized_text == "REFUND" and user.id == deal.seller_id:
                        # Seller refunds voluntarily
                        dispute.final_outcome = OutcomeType.REFUND
                        dispute.resolution_method = ResolutionMethod.SELF_REFUND
                        dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
                        db.commit()
                        
                        DarajaService.initiate_b2c_payout(db, deal, deal.buyer.phone_or_handle, deal.agreed_price, is_refund=True)
                        MetaService.send_text_message(db, platform, deal.buyer.phone_or_handle, f"🎉 The seller has voluntarily resolved the dispute and refunded the funds! Refund is processing.", deal.id)
                        session["deal_id"] = None
                        session["state"] = "IDLE"
                        return "You have voluntarily resolved the dispute and returned all funds to the buyer. Refund is processing."

        if normalized_text.startswith("PRICE"):
            if active_deal_id:
                deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
                if deal and deal.status in [DealStatus.DRAFT, DealStatus.AWAITING_CONFIRMATION, DealStatus.AWAITING_CONFIRMATION]:
                    if deal.seller_id == user.id:
                        price_match = re.search(r"\d+", text.replace(",", ""))
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
        if state == "AWAITING_RATING":
            if text.strip() in ["👍", "👎"]:
                score = 1.0 if text.strip() == "👍" else -1.0
                
                # Identify the ratee and deal
                deal = db.query(Deal).filter(Deal.id == active_deal_id).first()
                if not deal:
                    session["state"] = "IDLE"
                    session["deal_id"] = None
                    return "Error: Deal not found for this rating."
                
                ratee_id = deal.buyer_id if user.id == deal.seller_id else deal.seller_id
                
                # Check if this user already submitted a rating for this deal
                from backend.app.models import Rating, RatingSource
                existing_rating = db.query(Rating).filter(Rating.deal_id == deal.id, Rating.rater_id == user.id).first()
                if not existing_rating:
                    rating = Rating(
                        deal_id=deal.id,
                        rater_id=user.id,
                        ratee_id=ratee_id,
                        score=score,
                        rating_source=RatingSource.MANUAL
                    )
                    db.add(rating)
                    db.commit()
                else:
                    rating = existing_rating
                
                # Clear session state for this rater
                session["state"] = "IDLE"
                session["deal_id"] = None
                
                # Check if the other party has rated
                other_id = deal.seller_id if user.id == deal.buyer_id else deal.buyer_id
                other_rating = db.query(Rating).filter(Rating.deal_id == deal.id, Rating.rater_id == other_id).first()
                
                from backend.app.services.rating_service import RatingService
                if other_rating:
                    # Both have rated! Apply them and finalize
                    RatingService.apply_manual_rating(db, rating)
                    RatingService.apply_manual_rating(db, other_rating)
                    
                    # Notify the other party if they are still awaiting rating
                    other_user = db.query(User).filter(User.id == other_id).first()
                    MetaService.send_text_message(
                        db, platform, other_user.phone_or_handle,
                        "Feedback submitted! Mutual ratings have been finalized and applied.",
                        deal.id
                    )
                    return "Thank you for your feedback! Mutual ratings have been finalized and applied."
                else:
                    # Only one has rated
                    return "Feedback submitted! Your rating is hidden until the other party submits theirs or the window expires."
            else:
                return "Please reply with 👍 or 👎 to rate your experience."

        elif state == "AWAITING_DESC":
            session["draft_desc"] = text
            session["state"] = "AWAITING_PRICE"
            return "Got it. What is the agreed price in Kenyan Shillings (KES)? (Numbers only, e.g. 15000)"

        elif state == "AWAITING_PRICE":
            # Extract numbers
            price_match = re.search(r"\d+", text.replace(",", ""))
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
            from backend.app.models import DealType
            deal = Deal(
                seller_id=user.id,
                item_description=session["draft_desc"],
                agreed_price=session["draft_price"],
                deal_type=DealType.SHIPPED, # Default placeholder
                delivery_deadline=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=days),
                status=DealStatus.DRAFT,
                seller_confirmed=True  # Seller created it, so they auto-confirm
            )
            db.add(deal)
            db.commit()
            db.refresh(deal)
            
            session["deal_id"] = deal.id
            session["state"] = "AWAITING_BUYER_LINK"
            
            invite_text = f"JOIN_{deal.id}"
            invite_link = f"https://wa.me/{settings.BOT_WHATSAPP_NUMBER}?text={invite_text}"
            
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

        elif state == "AWAITING_TRANSACTION_TYPE":
            val = normalized_text.strip()
            from backend.app.models import DealType
            deal = db.query(Deal).filter(Deal.id == session["deal_id"]).first()
            if not deal:
                session["state"] = "IDLE"
                return "Error: No active deal found."
                
            if val == "1" or "digital" in val.lower():
                deal.transaction_type = "digital"
                deal.deal_type = DealType.DIGITAL
            elif val == "2" or "shipped" in val.lower():
                deal.transaction_type = "shipped"
                deal.deal_type = DealType.SHIPPED
            elif val == "3" or "handoff" in val.lower() or "person" in val.lower():
                deal.transaction_type = "handoff"
                deal.deal_type = DealType.HANDOFF
            elif val == "4" or "remote" in val.lower() or "service" in val.lower():
                deal.transaction_type = "remote_service"
                deal.deal_type = DealType.REMOTE_SERVICE
            else:
                return (
                    "Invalid choice. Please reply with a number 1 to 4:\n"
                    "1. Digital Deliverable\n"
                    "2. Shipped Goods (Courier)\n"
                    "3. Local In-Person Handoff\n"
                    "4. Remote Physical Service"
                )
            db.commit()
            
            if deal.transaction_type == "shipped":
                # Prompt seller for courier service configuration
                USER_SESSIONS[deal.seller.phone_or_handle] = {
                    "state": "AWAITING_COURIER_SELECTION",
                    "deal_id": deal.id
                }
                MetaService.send_text_message(
                    db, platform, deal.seller.phone_or_handle,
                    "The buyer has selected Shipped Goods. Please specify the exact courier service being used. Reply with the number:\n"
                    "1. Sendy\n"
                    "2. G4S\n"
                    "3. Boxleo\n"
                    "4. Kenya Post\n"
                    "5. Other",
                    deal.id
                )
                session["state"] = "AWAITING_COURIER_CONFIG_BY_SELLER"
                return "You selected Shipped Goods (Courier). Waiting for the seller to specify the courier service being used..."
            else:
                return cls._trigger_disclaimer_acknowledgements(db, platform, deal)

        elif state == "AWAITING_COURIER_SELECTION":
            val = normalized_text.strip()
            deal = db.query(Deal).filter(Deal.id == session["deal_id"]).first()
            if not deal:
                session["state"] = "IDLE"
                return "Error: No active deal found."
                
            couriers = {
                "1": "Sendy",
                "2": "G4S",
                "3": "Boxleo",
                "4": "Kenya Post"
            }
            if val in couriers:
                deal.courier_name = couriers[val]
                db.commit()
                return cls._trigger_disclaimer_acknowledgements(db, platform, deal)
            elif val == "5" or "other" in val.lower():
                session["state"] = "AWAITING_MANUAL_COURIER"
                return "Please enter the name of the courier service manually:"
            else:
                return (
                    "Invalid choice. Please reply with the number:\n"
                    "1. Sendy\n"
                    "2. G4S\n"
                    "3. Boxleo\n"
                    "4. Kenya Post\n"
                    "5. Other"
                )

        elif state == "AWAITING_MANUAL_COURIER":
            deal = db.query(Deal).filter(Deal.id == session["deal_id"]).first()
            if not deal:
                session["state"] = "IDLE"
                return "Error: No active deal found."
            deal.courier_name = text.strip()
            db.commit()
            return cls._trigger_disclaimer_acknowledgements(db, platform, deal)

        elif state == "AWAITING_DISCLAIMER_ACK":
            deal = db.query(Deal).filter(Deal.id == session["deal_id"]).first()
            if not deal:
                session["state"] = "IDLE"
                return "Error: No active deal found."
                
            if normalized_text == "I ACKNOWLEDGE":
                if user.id == deal.seller_id:
                    deal.seller_disclaimer_acknowledged = True
                elif user.id == deal.buyer_id:
                    deal.buyer_disclaimer_acknowledged = True
                db.commit()
                
                # Check if both have acknowledged
                if deal.seller_disclaimer_acknowledged and deal.buyer_disclaimer_acknowledged:
                    buyer_user = db.query(User).filter(User.id == deal.buyer_id).first()
                    
                    deal.status = DealStatus.AWAITING_CONFIRMATION
                    db.commit()
                    
                    USER_SESSIONS[deal.seller.phone_or_handle] = {"state": "IDLE", "deal_id": deal.id}
                    USER_SESSIONS[deal.buyer.phone_or_handle] = {"state": "IDLE", "deal_id": deal.id}
                    
                    if user.id == deal.seller_id:
                        # Current user is Seller. They get the return value.
                        # We send a text message to the Buyer.
                        MetaService.send_text_message(
                            db, platform, deal.buyer.phone_or_handle,
                            f"Deal '{deal.item_description[:30]}...' disclaimer acknowledged by both parties! "
                            f"We have sent an M-Pesa STK Push to your phone. Please check your phone and enter your PIN to complete payment.",
                            deal.id
                        )
                        try:
                            DarajaService.initiate_stk_push(db, deal, buyer_user.phone_or_handle)
                        except Exception as e:
                            logger.error(f"Failed to initiate STK push: {e}")
                            # Notify buyer of error
                            MetaService.send_text_message(
                                db, platform, deal.buyer.phone_or_handle,
                                f"Failed to initiate M-Pesa STK push: {str(e)}",
                                deal.id
                            )
                            return f"Disclaimer acknowledged, but M-Pesa STK Push trigger failed: {str(e)}"
                        
                        return (
                            f"You have acknowledged the disclaimer. Deal '{deal.item_description[:30]}...' "
                            f"disclaimer acknowledged by both parties! We are triggering the M-Pesa STK Push payment request to the buyer now."
                        )
                    else:
                        # Current user is Buyer. They get the return value.
                        # We send a text message to the Seller.
                        MetaService.send_text_message(
                            db, platform, deal.seller.phone_or_handle,
                            f"Deal '{deal.item_description[:30]}...' disclaimer acknowledged by both parties! "
                            f"We are triggering the M-Pesa STK Push payment request to the buyer now.",
                            deal.id
                        )
                        try:
                            DarajaService.initiate_stk_push(db, deal, buyer_user.phone_or_handle)
                            return (
                                f"You have acknowledged the disclaimer. We have sent an M-Pesa STK Push to your phone. "
                                f"Please check your phone and enter your PIN to complete payment."
                            )
                        except Exception as e:
                            logger.error(f"Failed to initiate STK push: {e}")
                            return f"You acknowledged the disclaimer, but M-Pesa STK Push trigger failed: {str(e)}"
                else:
                    other_party = "Buyer" if user.id == deal.seller_id else "Seller"
                    return f"Acknowledgement recorded! Waiting for the {other_party} to acknowledge the disclaimer."
            else:
                return "Please reply exactly 'I ACKNOWLEDGE' to agree to the disclaimer."

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
                            USER_SESSIONS[deal.seller.phone_or_handle] = {"state": "IDLE", "deal_id": deal.id}
                            USER_SESSIONS[deal.buyer.phone_or_handle] = {"state": "AWAITING_TRANSACTION_TYPE", "deal_id": deal.id}
                            
                            return (
                                "What is the transaction type? Reply with the number:\n"
                                "1. Digital Deliverable\n"
                                "2. Shipped Goods (Courier)\n"
                                "3. Local In-Person Handoff\n"
                                "4. Remote Physical Service"
                            )
                        else:
                            other_party = "Buyer" if user.id == deal.seller_id else "Seller"
                            return f"You confirmed the deal! Waiting for the {other_party} to confirm."

                    elif normalized_text == "REJECT":
                        deal.status = DealStatus.CANCELLED
                        db.commit()
                        CLOSED_DEAL_SESSIONS.add(deal.id)
                        
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
                        
                        if not deal.courier_name:
                            deal.courier_name = "Courier"
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
                        
                        # Create Dispute record immediately so it shows up in the queue!
                        dispute = Dispute(
                            deal_id=deal.id,
                            filed_by=user.id,
                            reason="Awaiting filer's detailed statement...",
                            tier=DisputeTier.TIER_2_AI
                        )
                        db.add(dispute)
                        db.commit()
                        
                        session["state"] = "AWAITING_DISPUTE_REASON"
                        
                        # Notify seller
                        MetaService.send_text_message(
                            db, platform, deal.seller.phone_or_handle,
                            f"⚠️ The buyer has disputed the delivery. HoldUntil escrow funds are locked. We will collect statements from both parties for moderation.",
                            deal.id
                        )
                        return (
                            "⚠️ WARNING: Filing false/meritless disputes is a violation of HoldUntil terms. "
                            "If physical delivery proof (courier tracking or photo evidence with matching verification code) is verified, the dispute will be resolved in the seller's favor, and your Trust Score will be penalized.\n\n"
                            "Escrow dispute registered. Please submit your reason before the automated moderator makes a judgment. What was wrong with the item or delivery?"
                        )

                elif session["state"] == "AWAITING_DISPUTE_REASON":
                    # Update the dispute record with the detailed reason
                    dispute = db.query(Dispute).filter(Dispute.deal_id == deal.id, Dispute.resolved_at == None).first()
                    if dispute:
                        dispute.reason = text
                        db.commit()
                    else:
                        dispute = Dispute(
                            deal_id=deal.id,
                            filed_by=user.id,
                            reason=text,
                            tier=DisputeTier.TIER_2_AI
                        )
                        db.add(dispute)
                        db.commit()

                    # Transition non-filer to response state
                    non_filer_user = deal.seller if user.id == deal.buyer_id else deal.buyer
                    
                    now = datetime.now(UTC).replace(tzinfo=None)
                    
                    if settings.SIMULATION_MODE:
                        dispute.response_window_deadline = now + timedelta(seconds=10)
                    else:
                        dispute.response_window_deadline = now + timedelta(hours=getattr(settings, "DISPUTE_RESPONSE_WINDOW_HOURS", 24))
                    
                    db.commit()
                    
                    # Update non-filer's session
                    non_filer_session = USER_SESSIONS.setdefault(non_filer_user.phone_or_handle, {"state": "IDLE", "deal_id": deal.id})
                    non_filer_session["state"] = "AWAITING_DISPUTE_RESPONSE"
                    non_filer_session["deal_id"] = deal.id
                    
                    session["state"] = "IDLE"
                    
                    # Notify non-filer
                    window_hours = getattr(settings, "DISPUTE_RESPONSE_WINDOW_HOURS", 24)
                    MetaService.send_text_message(
                        db, platform, non_filer_user.phone_or_handle,
                        f"A dispute has been filed on this deal. Reason given: {text}. "
                        f"You have {window_hours} hours to respond with your side and any supporting evidence before this is reviewed.",
                        deal.id
                    )
                    
                    return f"Your dispute has been recorded. We have notified the other party and given them {window_hours} hours to respond with their statement and evidence."

        return f"HoldUntil Escrow Bot. Type 'SELL' to start a new transaction, or 'HELP' for instructions."

    @classmethod
    def _handle_join_deal(cls, db: Session, user: User, deal_id: str, session: dict) -> str:
        deal = db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            return "Invalid invitation link or deal code."
        
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        if not seller:
            return "Seller not found for this deal."
            
        from backend.app.services.rating_service import RatingService
        
        # Get seller's profile summary for the buyer
        seller_summary = RatingService.get_profile_summary(db, seller)
        if seller_summary["is_new_trader"]:
            seller_intro = f"You're about to deal with Seller {seller.phone_or_handle}, a New Trader."
        else:
            badge_tag = " [PRO]" if seller_summary["has_badge"] else ""
            seller_intro = f"You're about to deal with Seller {seller.phone_or_handle}{badge_tag}, who has completed {seller_summary['completed_trades']} trades at {seller_summary['positive_rate']:.2f}% positive rating."
        
        if deal.status != DealStatus.DRAFT:
            if deal.buyer_id == user.id:
                session["deal_id"] = deal.id
                
                # Check if both have confirmed terms
                if deal.seller_confirmed and deal.buyer_confirmed:
                    if not deal.transaction_type:
                        session["state"] = "AWAITING_TRANSACTION_TYPE"
                        return (
                            "What is the transaction type? Reply with the number:\n"
                            "1. Digital Deliverable\n"
                            "2. Shipped Goods (Courier)\n"
                            "3. Local In-Person Handoff\n"
                            "4. Remote Physical Service"
                        )
                    elif deal.transaction_type == "shipped" and not deal.courier_name:
                        session["state"] = "IDLE"
                        return "Awaiting seller's courier selection."
                    elif not deal.buyer_disclaimer_acknowledged or not deal.seller_disclaimer_acknowledged:
                        session["state"] = "AWAITING_DISCLAIMER_ACK"
                        disclaimer = (
                            "⚠️ IMPORTANT TRANSACTION DISCLAIMER ⚠️\n\n"
                            "Evidence submitted during this transaction (photos, videos, tracking info) may be used to resolve a dispute if one arises. "
                            "Please take evidence seriously and ensure it clearly and honestly reflects what actually happened — "
                            "false or misleading evidence affects your trust score and may be treated as fraud.\n\n"
                            "Reply 'I ACKNOWLEDGE' to agree and proceed."
                        )
                        return disclaimer
                    else:
                        session["state"] = "IDLE"
                        return "This deal is funded and active. Please wait or proceed with the delivery."
                else:
                    session["state"] = "IDLE"
                    seller_session = USER_SESSIONS.setdefault(seller.phone_or_handle, {"state": "IDLE", "deal_id": None})
                    seller_session["deal_id"] = deal.id
                    
                    response = (
                        f"{seller_intro}\n\n"
                        f"🤝 You are joining a secure HoldUntil escrow deal!\n\n"
                        f"Seller: {seller.phone_or_handle}\n"
                        f"Item: {deal.item_description}\n"
                        f"Price: KES {deal.agreed_price:.2f}\n\n"
                        f"📜 **ESCROW AGREEMENT DISPUTE CLAUSE:**\n"
                        f"In the event of a dispute, either party may submit a Notice of Dispute within 5 days of shipment/deadline. "
                        f"Supporting evidence is required. HoldUntil shall freeze funds and facilitate a negotiation window. "
                        f"If unresolved, the Escrow Agent shall render a binding decision based on strict compliance. "
                        f"One appeal is permitted by replying 'APPEAL' (KES 200.00 fee applies), whose senior review is final "
                        f"within HoldUntil's internal dispute process. Governed under Kenyan Law.\n\n"
                        f"Reply 'CONFIRM' to accept details & initiate Safaricom M-Pesa STK Push payment.\n"
                        f"Reply 'REJECT' to decline."
                    )
                    return response
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
        seller_session = USER_SESSIONS.setdefault(seller.phone_or_handle, {"state": "IDLE", "deal_id": None})
        seller_session["deal_id"] = deal.id

        # Send details to buyer
        response = (
            f"{seller_intro}\n\n"
            f"🤝 You are joining a secure HoldUntil escrow deal!\n\n"
            f"Seller: {seller.phone_or_handle}\n"
            f"Item: {deal.item_description}\n"
            f"Price: KES {deal.agreed_price:.2f}\n\n"
            f"📜 **ESCROW AGREEMENT DISPUTE CLAUSE:**\n"
            f"In the event of a dispute, either party may submit a Notice of Dispute within 5 days of shipment/deadline. "
            f"Supporting evidence is required. HoldUntil shall freeze funds and facilitate a negotiation window. "
            f"If unresolved, the Escrow Agent shall render a binding decision based on strict compliance. "
            f"One appeal is permitted by replying 'APPEAL' (KES 200.00 fee applies), whose senior review is final "
            f"within HoldUntil's internal dispute process. Governed under Kenyan Law.\n\n"
            f"Reply 'CONFIRM' to accept details & initiate Safaricom M-Pesa STK Push payment.\n"
            f"Reply 'REJECT' to decline."
        )

        # Get buyer's profile summary for the seller notification
        buyer_summary = RatingService.get_profile_summary(db, user)
        if buyer_summary["is_new_trader"]:
            buyer_intro = f"Buyer {user.phone_or_handle} is a New Trader."
        else:
            badge_tag = " [PRO]" if buyer_summary["has_badge"] else ""
            buyer_intro = f"Buyer {user.phone_or_handle}{badge_tag} has completed {buyer_summary['completed_trades']} trades at {buyer_summary['positive_rate']:.2f}% positive rating."

        # Notify Seller
        MetaService.send_text_message(
            db, PlatformType.WHATSAPP, seller.phone_or_handle,
            f"Buyer {user.phone_or_handle} has joined the deal! {buyer_intro} Awaiting buyer's confirmation.",
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
            f"• **NO** — Dispute delivery (locks cash for moderator review).\n"
            f"• **MY PROFILE** — View your profile details.\n"
            f"• **UPDATE PAYOUT <number>** — Update payout M-Pesa number."
        )

    @classmethod
    def _trigger_disclaimer_acknowledgements(cls, db: Session, platform: PlatformType, deal: Deal) -> str:
        disclaimer = (
            "⚠️ IMPORTANT TRANSACTION DISCLAIMER ⚠️\n\n"
            "Evidence submitted during this transaction (photos, videos, tracking info) may be used to resolve a dispute if one arises. "
            "Please take evidence seriously and ensure it clearly and honestly reflects what actually happened — "
            "false or misleading evidence affects your trust score and may be treated as fraud.\n\n"
            "Reply 'I ACKNOWLEDGE' to agree and proceed."
        )
        
        deal.seller_disclaimer_acknowledged = False
        deal.buyer_disclaimer_acknowledged = False
        db.commit()
        
        seller_phone = deal.seller.phone_or_handle
        buyer_phone = deal.buyer.phone_or_handle
        
        USER_SESSIONS[seller_phone] = {"state": "AWAITING_DISCLAIMER_ACK", "deal_id": deal.id}
        USER_SESSIONS[buyer_phone] = {"state": "AWAITING_DISCLAIMER_ACK", "deal_id": deal.id}
        
        MetaService.send_text_message(db, platform, seller_phone, disclaimer, deal.id)
        if buyer_phone:
            MetaService.send_text_message(db, platform, buyer_phone, disclaimer, deal.id)
            
        return disclaimer

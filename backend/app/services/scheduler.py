from datetime import datetime, timedelta, UTC
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from backend.app.models import Deal, DealStatus, Evidence, Dispute, DisputeTier, OutcomeType, User
from backend.app.services.daraja_service import DarajaService
from backend.app.services.meta_service import MetaService
from backend.app.services.ai_service import AIService
import logging

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()

def run_tier_1_checks(db: Session):
    """
    Enforce Tier 1 Auto-Resolve rules.
    - Rule 1: No delivery evidence provided by seller past deadline -> auto-refund buyer
    - Rule 2: Seller provided delivery evidence (photo/tracking) + buyer silent past grace period -> auto-release to seller
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    logger.info("Running Tier 1 Auto-Resolve checks...")
    
    # Process expired rating windows
    from backend.app.services.rating_service import RatingService
    RatingService.close_pending_ratings(db)

    # Rule 1: No delivery evidence past deadline
    overdue_deals = db.query(Deal).filter(
        Deal.status == DealStatus.FUNDED,
        Deal.delivery_deadline < now
    ).all()

    for deal in overdue_deals:
        # Check if seller uploaded evidence
        evidence_count = db.query(Evidence).filter(Evidence.deal_id == deal.id, Evidence.submitted_by == deal.seller_id).count()
        
        if evidence_count == 0 and not deal.tracking_number:
            logger.info(f"Auto-resolving Deal {deal.id}: No seller evidence past deadline. Triggering buyer refund.")
            
            # File an auto-dispute
            dispute = Dispute(
                deal_id=deal.id,
                filed_by=deal.buyer_id,
                reason="Automatic resolution: Seller failed to ship or provide evidence before the delivery deadline.",
                tier=DisputeTier.TIER_1_AUTO,
                ai_decision=OutcomeType.REFUND,
                final_outcome=OutcomeType.REFUND,
                resolved_at=now
            )
            db.add(dispute)
            
            # Release funds to buyer (Refund)
            try:
                buyer_user = deal.buyer
                DarajaService.initiate_b2c_payout(db, deal, buyer_user.phone_or_handle, deal.agreed_price, is_refund=True)
                
                # Notify parties
                MetaService.send_text_message(
                    db, deal.seller.platform, deal.seller.phone_or_handle,
                    f"⚠️ Deal for '{deal.item_description[:30]}...' was automatically cancelled and refunded because no shipping evidence was submitted by the deadline.",
                    deal.id
                )
                MetaService.send_text_message(
                    db, deal.buyer.platform, deal.buyer.phone_or_handle,
                    f"🎉 You have been refunded KES {deal.agreed_price:.2f} as the seller did not ship by the deadline.",
                    deal.id
                )
            except Exception as pay_err:
                logger.error(f"Failed B2C payout for deal {deal.id} auto-refund: {pay_err}")
                
            deal.status = DealStatus.REFUNDED
            AIService.apply_dispute_outcome(db, deal, dispute, OutcomeType.REFUND)
            db.commit()
            
            RatingService.trigger_post_deal_rating(db, deal)

    # Rule 2: Seller provided evidence + buyer silent past grace period
    grace_limit = now - timedelta(hours=48)
    silent_deals = db.query(Deal).filter(
        Deal.status == DealStatus.SHIPPED,
        Deal.delivery_deadline < grace_limit
    ).all()

    for deal in silent_deals:
        # Verify seller provided evidence (tracking number or photos)
        evidence_count = db.query(Evidence).filter(Evidence.deal_id == deal.id, Evidence.submitted_by == deal.seller_id).count()
        has_evidence = (evidence_count > 0 or deal.tracking_number is not None)
        
        if has_evidence:
            logger.info(f"Auto-resolving Deal {deal.id}: Buyer silent past 48h grace period with evidence present. Releasing funds to seller.")
            
            # File auto dispute closure
            dispute = Dispute(
                deal_id=deal.id,
                filed_by=deal.seller_id,
                reason="Automatic resolution: Buyer was silent past the 48-hour grace period and delivery proof is present.",
                tier=DisputeTier.TIER_1_AUTO,
                ai_decision=OutcomeType.RELEASE,
                final_outcome=OutcomeType.RELEASE,
                resolved_at=now
            )
            db.add(dispute)
            
            # Release funds to seller
            try:
                seller_user = deal.seller
                DarajaService.initiate_b2c_payout(db, deal, seller_user.phone_or_handle, deal.agreed_price, is_refund=False)
                
                # Notify parties
                MetaService.send_text_message(
                    db, deal.seller.platform, deal.seller.phone_or_handle,
                    f"🎉 Escrow payment of KES {deal.agreed_price:.2f} has been released to you. The buyer was silent past the grace period.",
                    deal.id
                )
                MetaService.send_text_message(
                    db, deal.buyer.platform, deal.buyer.phone_or_handle,
                    f"ℹ️ HoldUntil: Escrow payment of KES {deal.agreed_price:.2f} was auto-released to the seller as the grace period expired.",
                    deal.id
                )
            except Exception as pay_err:
                logger.error(f"Failed B2C payout for deal {deal.id} auto-release: {pay_err}")
                
            deal.status = DealStatus.COMPLETED
            AIService.apply_dispute_outcome(db, deal, dispute, OutcomeType.RELEASE)
            db.commit()
            
            RatingService.trigger_post_deal_rating(db, deal)

    # Rule 3: Processing first-instance resolved disputes after appeal window
    from backend.app.config import settings
    # Sandbox uses 15 seconds, production uses APPEAL_WINDOW_HOURS
    if settings.SIMULATION_MODE:
        appeal_limit = now - timedelta(seconds=15)
        reminder_limit = now - timedelta(seconds=7.5)
    else:
        appeal_window_hours = getattr(settings, "APPEAL_WINDOW_HOURS", 72)
        appeal_limit = now - timedelta(hours=appeal_window_hours)
        reminder_limit = now - timedelta(hours=appeal_window_hours / 2.0)

    # 3.1 Send Proactive Reminders at halfway mark
    pending_reminders = db.query(Dispute).filter(
        Dispute.resolved_at != None,
        Dispute.resolved_at < reminder_limit,
        Dispute.is_appeal == False,
        Dispute.filer_satisfied == None,
        Dispute.appeal_reminder_sent == False
    ).all()

    for d in pending_reminders:
        d.appeal_reminder_sent = True
        db.commit()
        
        # Send reminder message to the filer
        from backend.app.models import PlatformType
        remaining_hours = int(getattr(settings, "APPEAL_WINDOW_HOURS", 72) / 2.0)
        reminder_msg = f"Reminder: You have {remaining_hours} hours remaining to appeal this decision if you disagree. Reply APPEAL to request a senior review (KES 200 fee applies)."
        
        MetaService.send_text_message(
            db, PlatformType.WHATSAPP, d.filer.phone_or_handle,
            reminder_msg, d.deal_id
        )
        logger.info(f"Sent proactive appeal reminder for Dispute {d.id} to user {d.filer.phone_or_handle}")

    # 3.2 Process lapsed appeal windows
    pending_releases = db.query(Dispute).filter(
        Dispute.resolved_at != None,
        Dispute.resolved_at < appeal_limit,
        Dispute.is_appeal == False,
        Dispute.filer_satisfied == None
    ).all()

    for d in pending_releases:
        # Check if the deal is still in DISPUTED status
        deal = db.query(Deal).filter(Deal.id == d.deal_id, Deal.status == DealStatus.DISPUTED).first()
        if deal:
            logger.info(f"Appeal window expired for Dispute {d.id}. Executing payout for verdict: {d.final_outcome}")
            
            try:
                seller = db.query(User).filter(User.id == deal.seller_id).first()
                buyer = db.query(User).filter(User.id == deal.buyer_id).first()
                
                if d.final_outcome == OutcomeType.RELEASE:
                    DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, deal.agreed_price, is_refund=False)
                    MetaService.send_text_message(db, deal.seller.platform, seller.phone_or_handle, f"Escrow payment of KES {deal.agreed_price:.2f} is being released to you as the appeal window has expired.", deal.id)
                    MetaService.send_text_message(db, deal.buyer.platform, buyer.phone_or_handle, f"Escrow payment released to seller. Rationale: Appeal window expired.", deal.id)
                elif d.final_outcome == OutcomeType.REFUND:
                    DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, deal.agreed_price, is_refund=True)
                    MetaService.send_text_message(db, deal.buyer.platform, buyer.phone_or_handle, f"Escrow refund of KES {deal.agreed_price:.2f} is being processed to you as the appeal window has expired.", deal.id)
                    MetaService.send_text_message(db, deal.seller.platform, seller.phone_or_handle, f"Escrow funds refunded to buyer. Rationale: Appeal window expired.", deal.id)
                elif d.final_outcome == OutcomeType.PARTIAL_SPLIT:
                    pct = d.partial_split_percentage or 50
                    seller_amt = (pct / 100.0) * deal.agreed_price
                    buyer_amt = deal.agreed_price - seller_amt
                    DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, seller_amt, is_refund=False)
                    DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, buyer_amt, is_refund=True)
                    MetaService.send_text_message(db, deal.seller.platform, seller.phone_or_handle, f"Escrow split payment of KES {seller_amt:.2f} is being released to you as the appeal window has expired.", deal.id)
                    MetaService.send_text_message(db, deal.buyer.platform, buyer.phone_or_handle, f"Escrow split refund of KES {buyer_amt:.2f} is being processed to you as the appeal window has expired.", deal.id)
                
                AIService.apply_dispute_outcome(db, deal, d, d.final_outcome, partial_split_percentage=d.partial_split_percentage)
                db.commit()
                
                RatingService.trigger_post_deal_rating(db, deal)
            except Exception as e:
                logger.error(f"Failed B2C payout for Rule 3 dispute {d.id}: {e}")

    # Rule 4: Processing lapsed dispute response windows
    pending_responses = db.query(Dispute).filter(
        Dispute.response_window_deadline != None,
        Dispute.response_window_deadline < now,
        Dispute.resolved_at == None,
        Dispute.human_moderator_id == None
    ).all()

    for d in pending_responses:
        logger.info(f"Dispute response window expired for Dispute {d.id}. Proceeding to review.")
        d.response_statement = "No response submitted by the non-filer (response window expired)."
        d.response_window_deadline = None
        db.commit()
        
        # Trigger AI resolution check
        try:
            AIService.run_moderation(db, d.id)
        except Exception as ai_err:
            logger.error(f"AI moderation trigger error on response window lapse: {ai_err}")
            
        from backend.app.services.chat_bot import ChatBotService
        ChatBotService.auto_assign_mediator(db, d)
        
        # Notify parties and reset non-filer's state
        deal = db.query(Deal).filter(Deal.id == d.deal_id).first()
        if deal:
            non_filer_user = db.query(User).filter(User.id == (deal.seller_id if d.filed_by == deal.buyer_id else deal.buyer_id)).first()
            if non_filer_user:
                from backend.app.services.chat_bot import USER_SESSIONS
                non_filer_session = USER_SESSIONS.get(non_filer_user.phone_or_handle)
                if non_filer_session and non_filer_session.get("state") == "AWAITING_DISPUTE_RESPONSE":
                    non_filer_session["state"] = "IDLE"
                    
            from backend.app.models import PlatformType
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, deal.buyer.phone_or_handle,
                f"Dispute response window has closed. The dispute for deal '{deal.item_description}' has proceeded to mediator review.",
                deal.id
            )
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, deal.seller.phone_or_handle,
                f"Dispute response window has closed. The dispute for deal '{deal.item_description}' has proceeded to mediator review.",
                deal.id
            )

def start_scheduler(SessionLocalFactory):
    """Start APScheduler jobs."""
    def job_wrapper():
        db = SessionLocalFactory()
        try:
            run_tier_1_checks(db)
        finally:
            db.close()

    scheduler.add_job(job_wrapper, "interval", minutes=5, id="tier1_auto_resolve")
    scheduler.start()
    logger.info("APScheduler initialized and running Tier 1 checks every 5 minutes.")

from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from backend.app.models import Deal, DealStatus, Evidence, Dispute, DisputeTier, OutcomeType
from backend.app.services.daraja_service import DarajaService
from backend.app.services.meta_service import MetaService
import logging

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler()

def run_tier_1_checks(db: Session):
    """
    Enforce Tier 1 Auto-Resolve rules.
    - Rule 1: No delivery evidence provided by seller past deadline -> auto-refund buyer
    - Rule 2: Seller provided delivery evidence (photo/tracking) + buyer silent past grace period -> auto-release to seller
    """
    now = datetime.utcnow()
    logger.info("Running Tier 1 Auto-Resolve checks...")

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
            db.commit()

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
            db.commit()

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

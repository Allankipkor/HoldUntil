import logging
from datetime import datetime, UTC, timedelta
from sqlalchemy.orm import Session
from backend.app.models import Deal, Dispute, DisputeTier, ResolutionMethod, Rating, RatingSource, User, PlatformType, DealStatus
from backend.app.services.meta_service import MetaService
from backend.app.services.chat_bot import USER_SESSIONS, CLOSED_DEAL_SESSIONS

logger = logging.getLogger("rating_service")

class RatingService:
    @classmethod
    def trigger_post_deal_rating(cls, db: Session, deal: Deal):
        """
        Main entry point triggered when a deal reaches completed/refunded.
        Routes to the correct path based on resolution type.
        """
        logger.info(f"Triggering rating flow check for Deal {deal.id}")
        dispute = db.query(Dispute).filter(Dispute.deal_id == deal.id).first()
        
        # Path 1: Clean undisputed completions (or Auto Tier 1 resolutions)
        if not dispute or dispute.tier == DisputeTier.TIER_1_AUTO:
            logger.info(f"Deal {deal.id} resolved clean or via Auto-resolution. Initiating manual rating flow.")
            cls._initiate_manual_rating_flow(db, deal)
            
        # Path 2: Informally resolved disputes (self-resolution)
        elif dispute.resolution_method in [ResolutionMethod.SELF_RELEASE, ResolutionMethod.SELF_REFUND]:
            logger.info(f"Deal {deal.id} was self-resolved by parties. Initiating manual rating flow.")
            cls._initiate_manual_rating_flow(db, deal)
            
        # Path 3: Moderator/Arbitrator resolved disputes
        elif dispute.resolution_method in [ResolutionMethod.HUMAN_FIRST_INSTANCE, ResolutionMethod.APPEAL]:
            logger.info(f"Deal {deal.id} resolved by staff. Automatic rating-equivalent signals handled silently. Closing deal session.")
            CLOSED_DEAL_SESSIONS.add(deal.id)

    @classmethod
    def _initiate_manual_rating_flow(cls, db: Session, deal: Deal):
        """
        Prompts both buyer and seller to manually rate each other.
        """
        from backend.app.models import ReminderTracker
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        buyer = db.query(User).filter(User.id == deal.buyer_id).first() if deal.buyer_id else None
        
        now = datetime.now(UTC).replace(tzinfo=None)

        if seller:
            USER_SESSIONS[seller.phone_or_handle] = {"state": "AWAITING_RATING", "deal_id": deal.id}
            seller_components = [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": f"Buyer {buyer.phone_or_handle if buyer else 'Buyer'}"},
                    {"type": "text", "text": deal.item_description[:30]}
                ]
            }]
            MetaService.send_template_message(
                db, seller.platform, seller.phone_or_handle,
                "feedback_rating_prompt", components=seller_components, deal_id=deal.id
            )
            # Create a reminder tracker entry for rating
            tracker = ReminderTracker(
                deal_id=deal.id,
                recipient_phone=seller.phone_or_handle,
                pending_action="rate_deal",
                reminder_count=0,
                last_sent_at=now,
                created_at=now
            )
            db.add(tracker)
            db.commit()
            
        if buyer:
            USER_SESSIONS[buyer.phone_or_handle] = {"state": "AWAITING_RATING", "deal_id": deal.id}
            buyer_components = [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": f"Seller {seller.phone_or_handle}"},
                    {"type": "text", "text": deal.item_description[:30]}
                ]
            }]
            MetaService.send_template_message(
                db, buyer.platform, buyer.phone_or_handle,
                "feedback_rating_prompt", components=buyer_components, deal_id=deal.id
            )
            # Create a reminder tracker entry for rating
            tracker = ReminderTracker(
                deal_id=deal.id,
                recipient_phone=buyer.phone_or_handle,
                pending_action="rate_deal",
                reminder_count=0,
                last_sent_at=now,
                created_at=now
            )
            db.add(tracker)
            db.commit()

    @classmethod
    def apply_manual_rating(cls, db: Session, rating: Rating):
        """
        Apply rating impact to ratee's trust score.
        - thumbs up (1.0): +1.0 trust score (max 100.0)
        - thumbs down (-1.0): -5.0 trust score (min 0.0)
        """
        ratee = db.query(User).filter(User.id == rating.ratee_id).first()
        if not ratee:
            return
            
        if rating.score == 1.0:
            ratee.trust_score = min(100.0, ratee.trust_score + 1.0)
        elif rating.score == -1.0:
            ratee.trust_score = max(0.0, ratee.trust_score - 5.0)
            
        rating.is_applied = True
        db.commit()
        
        CLOSED_DEAL_SESSIONS.add(rating.deal_id)
        logger.info(f"Deal {rating.deal_id} marked as closed after manual rating applied.")

    @classmethod
    def close_pending_ratings(cls, db: Session):
        """
        Finds pending ratings where 48-hour window has expired.
        Applies them, sets applied = True, and resets session states.
        """
        from backend.app.config import settings
        now = datetime.now(UTC).replace(tzinfo=None)
        
        # Sandbox uses 30 seconds, production uses 48 hours
        if settings.SIMULATION_MODE:
            window_limit = now - timedelta(seconds=30)
        else:
            window_limit = now - timedelta(hours=48)
            
        # Find all unapplied manual ratings that are older than the limit
        unapplied_ratings = db.query(Rating).filter(
            Rating.rating_source == RatingSource.MANUAL,
            Rating.is_applied == False,
            Rating.created_at < window_limit
        ).all()
        
        for r in unapplied_ratings:
            logger.info(f"Auto-closing pending rating {r.id} for ratee {r.ratee_id} as window expired.")
            cls.apply_manual_rating(db, r)
            
            # Reset rater/ratee session states to IDLE if still awaiting rating for this deal
            for handle, session in list(USER_SESSIONS.items()):
                if session.get("state") == "AWAITING_RATING" and session.get("deal_id") == r.deal_id:
                    USER_SESSIONS[handle] = {"state": "IDLE", "deal_id": None}

        # Clear expired rating trackers (skipped ratings)
        from backend.app.models import ReminderTracker
        expired_trackers = db.query(ReminderTracker).filter(
            ReminderTracker.pending_action == "rate_deal",
            ReminderTracker.created_at < window_limit
        ).all()
        
        for tracker in expired_trackers:
            session = USER_SESSIONS.get(tracker.recipient_phone)
            if session and session.get("state") == "AWAITING_RATING" and session.get("deal_id") == tracker.deal_id:
                logger.info(f"Skipping pending rating for user {tracker.recipient_phone} on deal {tracker.deal_id} (window expired)")
                USER_SESSIONS[tracker.recipient_phone] = {"state": "IDLE", "deal_id": None}
            db.delete(tracker)
        db.commit()

    @classmethod
    def get_profile_summary(cls, db: Session, user: User) -> dict:
        """
        Modelled on Binance P2P profile. Returns badge, trade count, completion rate,
        and positive rate. Respects the configurable 'New Trader' threshold.
        """
        from backend.app.config import settings
        from backend.app.models import Deal, DealStatus, Payment, PaymentStatus, Rating, RatingSource, Dispute
        
        # 1. Calculate Completed trades count (reaching COMPLETED or REFUNDED status)
        completed_trades = db.query(Deal).filter(
            ((Deal.seller_id == user.id) | (Deal.buyer_id == user.id)),
            Deal.status.in_([DealStatus.COMPLETED, DealStatus.REFUNDED])
        ).count()
        
        # Determine configurable threshold (default to 3)
        min_trades_threshold = getattr(settings, "MIN_TRADES_FOR_PROFILE_STATS", 3)
        
        is_new_trader = completed_trades < min_trades_threshold
        
        # Unresolved disputes
        unresolved_disputes = db.query(Dispute).join(Deal).filter(
            ((Deal.seller_id == user.id) | (Deal.buyer_id == user.id)),
            Dispute.resolved_at == None
        ).count()
        
        # Verified badge: 10+ completed deals, trust score >= 85, and zero unresolved disputes
        has_badge = (completed_trades >= settings.MIN_DEALS_FOR_BADGE) and (user.trust_score >= 85.0) and (unresolved_disputes == 0)
        
        if is_new_trader:
            return {
                "phone_or_handle": user.phone_or_handle,
                "is_new_trader": True,
                "has_badge": False,
                "completed_trades": completed_trades,
                "completion_rate": None,
                "positive_rate": None,
                "trust_score": user.trust_score,
                "display_text": f"{user.phone_or_handle} · New Trader"
            }
            
        # 2. Calculate Completion Rate
        # EXCLUDING deals cancelled before funding (CANCELLED status and no successful paid payment)
        all_user_deals = db.query(Deal).filter(
            ((Deal.seller_id == user.id) | (Deal.buyer_id == user.id)),
            Deal.status != DealStatus.DRAFT
        ).all()
        
        valid_deals_count = 0
        successful_deals_count = 0
        
        for deal in all_user_deals:
            # Check if cancelled before funding
            if deal.status == DealStatus.CANCELLED:
                has_paid_payment = any(
                    p.status in [
                        PaymentStatus.PAID,
                        PaymentStatus.PAYOUT_PROCESSING,
                        PaymentStatus.PAYOUT_COMPLETED,
                        PaymentStatus.REFUND_PROCESSING,
                        PaymentStatus.REFUND_COMPLETED
                    ] for p in deal.payments
                )
                if not has_paid_payment:
                    # Exclude cancelled-before-funding
                    continue
            
            valid_deals_count += 1
            if deal.status in [DealStatus.COMPLETED, DealStatus.REFUNDED]:
                successful_deals_count += 1
                
        completion_rate = (successful_deals_count / valid_deals_count * 100.0) if valid_deals_count > 0 else 100.0
        
        # 3. Calculate Positive Rating Rate (using only applied/finalized ratings)
        total_ratings = db.query(Rating).filter(Rating.ratee_id == user.id, Rating.is_applied == True).count()
        positive_ratings = db.query(Rating).filter(Rating.ratee_id == user.id, Rating.score == 1.0, Rating.is_applied == True).count()
        
        positive_rate = (positive_ratings / total_ratings * 100.0) if total_ratings > 0 else 100.0
        
        # Format display text (two-line format)
        badge_tag = " [PRO]" if has_badge else ""
        display_text = (
            f"{user.phone_or_handle}{badge_tag}\n"
            f"Trades: {completed_trades} Trades ({completion_rate:.2f}%) | 👍 {positive_rate:.2f}%"
        )
        
        return {
            "phone_or_handle": user.phone_or_handle,
            "is_new_trader": False,
            "has_badge": has_badge,
            "completed_trades": completed_trades,
            "completion_rate": round(completion_rate, 2),
            "positive_rate": round(positive_rate, 2),
            "trust_score": user.trust_score,
            "display_text": display_text
        }

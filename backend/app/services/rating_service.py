import logging
from datetime import datetime, UTC, timedelta
from sqlalchemy.orm import Session
from backend.app.models import Deal, Dispute, DisputeTier, ResolutionMethod, Rating, RatingSource, User, PlatformType, DealStatus
from backend.app.services.meta_service import MetaService
from backend.app.services.chat_bot import USER_SESSIONS

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
            logger.info(f"Deal {deal.id} resolved by staff. Automatic rating-equivalent signals handled silently.")
            # Automatic signals logged under AIService.apply_dispute_outcome
            pass

    @classmethod
    def _initiate_manual_rating_flow(cls, db: Session, deal: Deal):
        """
        Prompts both buyer and seller to manually rate each other.
        """
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        buyer = db.query(User).filter(User.id == deal.buyer_id).first() if deal.buyer_id else None
        
        if seller:
            USER_SESSIONS[seller.phone_or_handle] = {"state": "AWAITING_RATING", "deal_id": deal.id}
            MetaService.send_text_message(
                db, seller.platform, seller.phone_or_handle,
                f"How was your experience with Buyer {buyer.phone_or_handle if buyer else 'Buyer'}? Reply 👍 or 👎.",
                deal.id
            )
            
        if buyer:
            USER_SESSIONS[buyer.phone_or_handle] = {"state": "AWAITING_RATING", "deal_id": deal.id}
            MetaService.send_text_message(
                db, buyer.platform, buyer.phone_or_handle,
                f"How was your experience with Seller {seller.phone_or_handle}? Reply 👍 or 👎.",
                deal.id
            )

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

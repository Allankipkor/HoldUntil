import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

from backend.app.database import Base
from backend.app.models import User, Deal, DealStatus, Payment, PaymentStatus, ChatLog, PlatformType, UserRole
from backend.app.services.chat_bot import ChatBotService, USER_SESSIONS
from backend.app.services.couriers import verify_tracking
from backend.app.services.image_service import ImageService
from backend.app.services.ai_service import AIService

# Set up in-memory database for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

def test_user_creation(db_session):
    """Verify that users can be created and queried successfully."""
    user = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254711111111")
    assert user.phone_or_handle == "254711111111"
    assert user.trust_score == 100.0
    assert user.role == UserRole.USER

def test_deal_creation_dialogue_flow(db_session):
    """Test the seller setup dialogue chain (SELL -> Desc -> Price -> Days)."""
    seller_phone = "254711111111"
    
    # Step 1: Initialize
    r1 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SELL")
    assert "description" in r1.lower()
    assert USER_SESSIONS[seller_phone]["state"] == "AWAITING_DESC"

    # Step 2: Description
    r2 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "HP Pavilion Laptop")
    assert "agreed price" in r2.lower()
    assert USER_SESSIONS[seller_phone]["state"] == "AWAITING_PRICE"
    assert USER_SESSIONS[seller_phone]["draft_desc"] == "HP Pavilion Laptop"

    # Step 3: Price
    r3 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "KES 15000")
    assert "delivery" in r3.lower()
    assert USER_SESSIONS[seller_phone]["state"] == "AWAITING_TIMELINE"
    assert USER_SESSIONS[seller_phone]["draft_price"] == 15000.0

    # Step 4: Timeline
    r4 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "3 days")
    assert "invite" in r4.lower()
    assert "JOIN_" in r4
    
    # Verify deal is registered in database
    deal = db_session.query(Deal).filter(Deal.item_description == "HP Pavilion Laptop").first()
    assert deal is not None
    assert deal.agreed_price == 15000.0
    assert deal.status == DealStatus.DRAFT
    assert deal.seller_confirmed is True

def test_buyer_join_and_confirm(db_session):
    """Test buyer joining the deal draft via invite code and confirming terms."""
    seller_phone = "254711111111"
    buyer_phone = "254722222222"
    
    # Setup deal first
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SELL")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "iPhone")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "45000")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "2")
    
    deal = db_session.query(Deal).filter(Deal.item_description == "iPhone").first()
    deal_id = deal.id

    # Buyer joins
    join_reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, f"JOIN_{deal_id}")
    assert "agreement" in join_reply.lower() or "dispute" in join_reply.lower()
    assert "confirm" in join_reply.lower()
    
    # Reload deal from DB
    db_session.refresh(deal)
    assert deal.buyer_id is not None
    assert deal.status == DealStatus.AWAITING_CONFIRMATION

    # Buyer confirms terms
    confirm_reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "CONFIRM")
    assert "transaction type" in confirm_reply.lower()
    
    # Buyer selects Shipped Goods (2)
    type_reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "2")
    assert "waiting" in type_reply.lower()
    
    # Seller selects Sendy (1)
    courier_reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "1")
    assert "disclaimer" in courier_reply.lower()
    
    # Acknowledge disclaimer
    ack_buyer = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "I ACKNOWLEDGE")
    assert "waiting" in ack_buyer.lower() or "acknowledge" in ack_buyer.lower()
    
    ack_seller = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "I ACKNOWLEDGE")
    assert "m-pesa" in ack_seller.lower() or "stk" in ack_seller.lower()

def test_courier_tracking_plugin():
    """Verify courier APIs pluggable tracking number routing."""
    # Sendy Check
    r_sendy_ok = verify_tracking("sendy", "SDY-100")
    assert r_sendy_ok["status"] == "delivered"
    
    r_sendy_fail = verify_tracking("sendy", "SDY-FAIL")
    assert r_sendy_fail["status"] == "invalid"

    # Boxleo Check
    r_boxleo_ok = verify_tracking("boxleo", "BXL-900")
    assert r_boxleo_ok["status"] == "delivered"

    # Unknown
    r_unknown = verify_tracking("courier_fake", "123")
    assert r_unknown["status"] == "unknown"

def test_image_verification_checks():
    """Test perceptual image hashing lookup logic and code verification helpers."""
    # Heuristics checks
    assert ImageService.verify_dynamic_code("img_123.jpg", "HU-ABC1") is True
    assert ImageService.verify_dynamic_code("img_FAIL_CODE.jpg", "HU-ABC1") is False

def test_ai_heuristics_dispute_resolution(db_session):
    """Test local rule-based heuristic resolution fallback when Gemini key is not configured."""
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254711111111")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254722222222")
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Item",
        agreed_price=5000.0,
        status=DealStatus.DISPUTED
    )
    db_session.add(deal)
    db_session.commit()

    # Case A: No evidence provided at all -> should refund buyer
    res_a = AIService._run_heuristics(deal, None, [])
    assert res_a["outcome"] == "refund"
    assert res_a["confidence"] == 0.99

def test_price_edit_and_cancel_commands(db_session):
    """Test updating the deal price and cancelling the session dialogue via command inputs."""
    seller_phone = "254711111111"
    
    # 1. Create a draft deal
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SELL")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "iPhone 17 Pro")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "14,000")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "3")
    
    deal = db_session.query(Deal).filter(Deal.item_description == "iPhone 17 Pro").first()
    assert deal.agreed_price == 14000.0
    
    # 2. Seller corrects the price to 230,000 (with comma)
    r_price = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "PRICE 230,000")
    db_session.refresh(deal)
    assert deal.agreed_price == 230000.0
    assert "230000" in r_price
    
    # 3. Seller resets/cancels the deal
    r_cancel = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "CANCEL")
    db_session.refresh(deal)
    assert deal.status == DealStatus.CANCELLED
    assert "cancelled" in r_cancel.lower()
    assert USER_SESSIONS[seller_phone]["state"] == "IDLE"

def test_dispute_resolution_and_trust_adjustment(db_session):
    """Test full dispute flow: AI moderation (recommender only), first-instance resolution, appeal request, and arbitrator resolution."""
    from backend.app.models import Dispute, DisputeTier, OutcomeType, User, UserRole
    
    # 1. Setup users and a funded deal
    seller_phone = "254711111111"
    buyer_phone = "254722222222"
    
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, seller_phone)
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, buyer_phone)
    
    # Ensure default trust scores
    seller.trust_score = 100.0
    buyer.trust_score = 100.0
    db_session.commit()
    
    # Run the setup flow to get an active funded deal
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SELL")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "Sony WH-1000XM5")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "30000")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "3")
    
    deal = db_session.query(Deal).filter(Deal.item_description == "Sony WH-1000XM5").first()
    assert deal is not None
    
    # Buyer joins & confirms
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, f"JOIN_{deal.id}")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "CONFIRM")
    # New flow post-confirm steps
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "2")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "1")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "I ACKNOWLEDGE")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "I ACKNOWLEDGE")
    
    # Force payment confirmation to set status = FUNDED
    from backend.app.routes.dashboard import simulate_mpesa_payment
    p = Payment(deal_id=deal.id, stk_push_ref="test_ref", amount=30000.0, status=PaymentStatus.PENDING)
    db_session.add(p)
    db_session.commit()
    
    simulate_mpesa_payment(checkout_id="test_ref", db=db_session)
    db_session.refresh(deal)
    assert deal.status == DealStatus.FUNDED
    
    # Seller ships (updates status = SHIPPED)
    from backend.app.routes.dashboard import upload_simulated_evidence
    upload_simulated_evidence(deal_id=deal.id, sender_id=seller.id, photo_name="package_with_code.jpg", in_app_captured=True, db=db_session)
    db_session.refresh(deal)
    assert deal.status == DealStatus.SHIPPED
    
    # 2. Buyer disputes the deal by sending NO (updates status = DISPUTED)
    reply_no = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "NO")
    assert "WARNING" in reply_no
    assert "dispute" in reply_no.lower()
    
    db_session.refresh(deal)
    assert deal.status == DealStatus.DISPUTED
    
    # A dispute record should have been created
    dispute = db_session.query(Dispute).filter(Dispute.deal_id == deal.id).first()
    assert dispute is not None
    assert dispute.tier == DisputeTier.TIER_2_AI
    assert dispute.filed_by == buyer.id
    
    # 3. Buyer submits dispute reason -> triggers AI Moderator recommender-only check
    reply_reason = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "The box is empty!")
    assert "analyzing" in reply_reason.lower()
    
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # Recommender-only checks: resolved_at and final_outcome remain NULL, deal remains DISPUTED
    assert dispute.resolved_at is None
    assert dispute.final_outcome is None
    assert dispute.ai_decision == OutcomeType.RELEASE # Heuristics fallback recommends RELEASE
    assert deal.status == DealStatus.DISPUTED

    # 4. Resolve dispute manually (first-instance)
    from backend.app.routes.dashboard import resolve_dispute_manually
    mediator = User(phone_or_handle="MOD_1", role=UserRole.MODERATOR, platform=PlatformType.WHATSAPP)
    db_session.add(mediator)
    db_session.commit()
    
    resolve_dispute_manually(dispute_id=dispute.id, outcome="release", reasoning="Heuristic matched", resolver_id=mediator.id, db=db_session)
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # First-instance: resolved_at is set, outcome is RELEASE, but deal.status remains DISPUTED (payout deferred)
    assert dispute.resolved_at is not None
    assert dispute.final_outcome == OutcomeType.RELEASE
    assert deal.status == DealStatus.DISPUTED

    # 5. Buyer files an APPEAL
    reply_appeal = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "APPEAL")
    assert "appealed successfully" in reply_appeal.lower()
    
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # Appeal: reopens dispute (resolved_at is None, is_appeal is True)
    assert dispute.is_appeal is True
    assert dispute.appeal_requested_by == buyer.id
    assert dispute.resolved_at is None
    assert deal.status == DealStatus.DISPUTED

    # 6. Arbitrator resolves the appeal
    arbitrator = User(phone_or_handle="ARB_1", role=UserRole.ARBITRATOR, platform=PlatformType.WHATSAPP)
    db_session.add(arbitrator)
    db_session.commit()
    
    # Resolve appeal to REFUND (overturning RELEASE)
    resolve_dispute_manually(dispute_id=dispute.id, outcome="refund", reasoning="Arbitrator verdict", resolver_id=arbitrator.id, db=db_session)
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # Final resolution: resolved_at is set, outcome is REFUND, deal.status is REFUNDED (immediate payout)
    assert dispute.resolved_at is not None
    assert dispute.final_outcome == OutcomeType.REFUND
    assert deal.status == DealStatus.REFUNDED
    
    # Overturned appeal fee refund should be completed in simulation mode
    assert dispute.appeal_fee_refunded is True
    assert dispute.appeal_fee_refund_status == "completed"

def test_in_app_evidence_enforcement_and_video_call(db_session):
    """Test in-app camera verification enforcement and Type 4 video call logging."""
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254711111111")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254722222222")
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Remote Physical Repairs",
        agreed_price=5000.0,
        status=DealStatus.FUNDED,
        transaction_type="remote_service"
    )
    db_session.add(deal)
    db_session.commit()
    
    from fastapi import HTTPException
    from backend.app.routes.dashboard import upload_simulated_evidence, log_simulated_video_call
    from backend.app.models import Evidence
    
    # 1. Attempt upload-evidence from gallery (in_app_captured=False) -> should fail
    with pytest.raises(HTTPException) as excinfo:
        upload_simulated_evidence(
            deal_id=deal.id,
            sender_id=seller.id,
            photo_name="repaired_wall.jpg",
            in_app_captured=False,
            db=db_session
        )
    assert excinfo.value.status_code == 400
    assert "in-app camera" in excinfo.value.detail.lower()

    # 2. Log Type 4 video call
    res = log_simulated_video_call(
        deal_id=deal.id,
        sender_id=seller.id,
        duration_seconds=120,
        buyer_present=True,
        db=db_session
    )
    assert res["status"] == "video_call_logged"
    
    # Verify evidence registered
    ev = db_session.query(Evidence).filter(Evidence.deal_id == deal.id).first()
    assert ev is not None
    assert ev.in_app_captured is True
    assert ev.video_call_log is not None
    assert ev.video_call_log["duration_seconds"] == 120

def test_dispute_notification_no_leak(db_session):
    """Ensure that user-facing dispute chat logs do NOT leak the AI recommendation or confidence score, but it is stored in the Dispute model."""
    from backend.app.models import Dispute, DisputeTier, OutcomeType, ChatLog
    from backend.app.services.ai_service import AIService
    
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254711111111")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254722222222")
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Laptop",
        agreed_price=10000.0,
        status=DealStatus.DISPUTED
    )
    db_session.add(deal)
    db_session.commit()
    
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Screen arrived broken",
        tier=DisputeTier.TIER_2_AI
    )
    db_session.add(dispute)
    db_session.commit()
    
    # Trigger moderation
    AIService.run_moderation(db_session, dispute.id)
    
    # Refresh deal and dispute from database
    db_session.refresh(deal)
    db_session.refresh(dispute)
    
    # 1. Assert backend internal data is fully preserved
    assert dispute.ai_decision is not None
    assert dispute.ai_confidence is not None
    assert dispute.ai_reasoning is not None
    
    # 2. Check the chat logs written to database for this deal
    logs = db_session.query(ChatLog).filter(ChatLog.deal_id == deal.id).all()
    assert len(logs) > 0
    
    for log in logs:
        content = log.message_content
        # Ensure it does NOT leak "AI Recommendation", "Confidence", or the actual recommendation value in user-facing message
        assert "AI Recommendation" not in content
        assert "Confidence" not in content
        
        # Verify it has the exact wording requested
        if "🤖 HoldUntil Automated Check" in content:
            assert "Your dispute has been routed to a Human Moderator" in content
            assert "We have analyzed the deal and logged the supporting evidence" in content
        elif "🤖 HoldUntil Notification" in content:
            assert "A dispute has been filed. The case has been analyzed and routed to a human mediator" in content

def test_path1_clean_completion_manual_rating(db_session):
    """Path 1: Undisputed clean completions should trigger double-blind manual rating flow."""
    from backend.app.services.rating_service import RatingService
    from backend.app.services.chat_bot import USER_SESSIONS
    from backend.app.models import Rating, RatingSource
    
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000001")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000002")
    
    # Reset initial trust
    seller.trust_score = 90.0
    buyer.trust_score = 90.0
    db_session.commit()
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Laptop Clean",
        agreed_price=15000.0,
        status=DealStatus.COMPLETED
    )
    db_session.add(deal)
    db_session.commit()
    
    # 1. Trigger rating flow
    RatingService.trigger_post_deal_rating(db_session, deal)
    
    # Sessions should be AWAITING_RATING
    assert USER_SESSIONS[seller.phone_or_handle]["state"] == "AWAITING_RATING"
    assert USER_SESSIONS[buyer.phone_or_handle]["state"] == "AWAITING_RATING"
    
    # 2. Seller rates buyer 👍
    res_seller = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller.phone_or_handle, "👍")
    assert "hidden until the other party" in res_seller
    
    # Rating logged but not applied
    r_seller = db_session.query(Rating).filter(Rating.rater_id == seller.id).first()
    assert r_seller is not None
    assert r_seller.score == 1.0
    assert r_seller.is_applied is False
    assert buyer.trust_score == 90.0
    
    # 3. Buyer rates seller 👍
    res_buyer = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer.phone_or_handle, "👍")
    assert "finalized and applied" in res_buyer
    
    # Verify both finalized
    db_session.refresh(seller)
    db_session.refresh(buyer)
    assert buyer.trust_score == 91.0  # +1.0 trust score from manual 👍
    assert seller.trust_score == 91.0
    
    # Verify Rating records marked applied
    r_buyer = db_session.query(Rating).filter(Rating.rater_id == buyer.id).first()
    assert r_buyer.is_applied is True
    db_session.refresh(r_seller)
    assert r_seller.is_applied is True


def test_path2_informally_resolved_dispute_manual_rating(db_session):
    """Path 2: Informally resolved dispute (SELF_RELEASE) triggers the same manual rating flow."""
    from backend.app.services.rating_service import RatingService
    from backend.app.services.chat_bot import USER_SESSIONS
    from backend.app.models import Dispute, DisputeTier, ResolutionMethod, Rating, RatingSource
    
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000003")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000004")
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Dispute Self Resolve",
        agreed_price=5000.0,
        status=DealStatus.DISPUTED
    )
    db_session.add(deal)
    db_session.commit()
    
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Delay",
        tier=DisputeTier.TIER_2_AI
    )
    db_session.add(dispute)
    db_session.commit()
    
    # Simulate Buyer RELEASE
    USER_SESSIONS[buyer.phone_or_handle] = {"state": "IDLE", "deal_id": deal.id}
    res_release = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer.phone_or_handle, "RELEASE")
    assert "released all funds" in res_release
    
    db_session.refresh(dispute)
    assert dispute.resolution_method == ResolutionMethod.SELF_RELEASE
    
    # Simulate payout completing
    deal.status = DealStatus.COMPLETED
    db_session.commit()
    RatingService.trigger_post_deal_rating(db_session, deal)
    
    # Verify manual rating initiated
    assert USER_SESSIONS[seller.phone_or_handle]["state"] == "AWAITING_RATING"
    assert USER_SESSIONS[buyer.phone_or_handle]["state"] == "AWAITING_RATING"


def test_path3_moderator_resolved_dispute_automatic_rating(db_session):
    """Path 3: Dispute resolved by mediator/arbitrator does NOT trigger manual rating prompt, logs dispute_outcome rating silently."""
    from backend.app.services.rating_service import RatingService
    from backend.app.services.chat_bot import USER_SESSIONS
    from backend.app.models import Dispute, DisputeTier, ResolutionMethod, Rating, RatingSource, OutcomeType
    
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000005")
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000006")
    seller.trust_score = 90.0
    buyer.trust_score = 90.0
    db_session.commit()
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Dispute Moderator Resolve",
        agreed_price=8000.0,
        status=DealStatus.DISPUTED
    )
    db_session.add(deal)
    db_session.commit()
    
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Failed deliverable",
        tier=DisputeTier.TIER_3_HUMAN,
        resolution_method=ResolutionMethod.HUMAN_FIRST_INSTANCE
    )
    db_session.add(dispute)
    db_session.commit()
    
    # Simulate first-instance resolution finalize (via apply_dispute_outcome)
    from backend.app.services.ai_service import AIService
    AIService.apply_dispute_outcome(db_session, deal, dispute, OutcomeType.RELEASE)
    
    deal.status = DealStatus.COMPLETED
    db_session.commit()
    
    # Trigger rating flow
    RatingService.trigger_post_deal_rating(db_session, deal)
    
    # Verify NO manual rating session state was set
    assert USER_SESSIONS.get(seller.phone_or_handle, {}).get("state") != "AWAITING_RATING"
    assert USER_SESSIONS.get(buyer.phone_or_handle, {}).get("state") != "AWAITING_RATING"
    
    # Verify automatic dispute outcome rating records were logged
    ratings = db_session.query(Rating).filter(Rating.deal_id == deal.id).all()
    assert len(ratings) == 2
    
    for r in ratings:
        assert r.rating_source == RatingSource.DISPUTE_OUTCOME
        assert r.rater_id is None
        assert r.is_applied is True
        if r.ratee_id == seller.id:
            assert r.score == 1.0  # Upheld party gets positive-equivalent signal
        else:
            assert r.score == -1.0  # At-fault party gets negative-equivalent signal

def test_new_trader_profile_summary(db_session):
    """Verify that a user with fewer than 3 completed trades is labeled as New Trader with stats hidden."""
    from backend.app.services.rating_service import RatingService
    from backend.app.config import settings
    
    user = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254799000101")
    settings.MIN_TRADES_FOR_PROFILE_STATS = 3
    
    # 0 Completed deals
    summary = RatingService.get_profile_summary(db_session, user)
    assert summary["is_new_trader"] is True
    assert summary["completion_rate"] is None
    assert summary["positive_rate"] is None
    assert "New Trader" in summary["display_text"]
    assert summary["has_badge"] is False

    # Add 2 completed deals (still below threshold of 3)
    for i in range(2):
        deal = Deal(
            seller_id=user.id,
            item_description=f"Deal {i}",
            agreed_price=100.0,
            status=DealStatus.COMPLETED
        )
        db_session.add(deal)
    db_session.commit()
    
    summary = RatingService.get_profile_summary(db_session, user)
    assert summary["is_new_trader"] is True
    assert summary["completed_trades"] == 2
    assert summary["completion_rate"] is None
    assert summary["positive_rate"] is None
    
def test_full_trader_profile_summary(db_session):
    """Verify that crossing the threshold shows full statistics and correct P2P display format."""
    from backend.app.services.rating_service import RatingService
    from backend.app.config import settings
    
    user = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254799000102")
    settings.MIN_TRADES_FOR_PROFILE_STATS = 3
    settings.MIN_DEALS_FOR_BADGE = 10
    
    # Create 3 completed deals
    for i in range(3):
        deal = Deal(
            seller_id=user.id,
            item_description=f"Deal {i}",
            agreed_price=100.0,
            status=DealStatus.COMPLETED
        )
        db_session.add(deal)
    db_session.commit()
    
    # Should have crossed the threshold
    summary = RatingService.get_profile_summary(db_session, user)
    assert summary["is_new_trader"] is False
    assert summary["completed_trades"] == 3
    assert summary["completion_rate"] == 100.0
    assert summary["positive_rate"] == 100.0 # Default fallback if 0 ratings
    
    # Check layout format contains name, trades count, rates but no badge
    assert "\nTrades: 3 Trades (100.00%) | 👍 100.00%" in summary["display_text"]
    assert "[PRO]" not in summary["display_text"]

def test_cancellation_exclusions_in_completion_rate(db_session):
    """Verify that cancelled-before-funding deals are excluded from completion rate, but cancelled-after-funding are included."""
    from backend.app.services.rating_service import RatingService
    
    user = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254799000103")
    
    # 3 completed deals
    for i in range(3):
        deal = Deal(seller_id=user.id, item_description=f"Deal {i}", agreed_price=100.0, status=DealStatus.COMPLETED)
        db_session.add(deal)
        
    # 1 cancelled BEFORE funding (no payments)
    deal_cancelled_before = Deal(seller_id=user.id, item_description="Cancelled Before", agreed_price=100.0, status=DealStatus.CANCELLED)
    db_session.add(deal_cancelled_before)
    
    # 1 cancelled AFTER funding (has a paid payment)
    deal_cancelled_after = Deal(seller_id=user.id, item_description="Cancelled After", agreed_price=100.0, status=DealStatus.CANCELLED)
    db_session.add(deal_cancelled_after)
    db_session.flush()
    
    payment = Payment(deal_id=deal_cancelled_after.id, amount=100.0, status=PaymentStatus.PAID)
    db_session.add(payment)
    db_session.commit()
    
    # Total valid deals (denominator) should be: 3 completed + 1 cancelled-after = 4 deals
    # (cancelled-before is excluded)
    # Successful deals (numerator) should be: 3 completed
    # Completion rate: 3/4 = 75.0%
    summary = RatingService.get_profile_summary(db_session, user)
    assert summary["is_new_trader"] is False
    assert summary["completed_trades"] == 3
    assert summary["completion_rate"] == 75.0

def test_dispute_outcomes_in_positive_rate(db_session):
    """Verify positive rate combines manual ratings and dispute outcomes, filtering out unapplied manual ratings."""
    from backend.app.services.rating_service import RatingService
    from backend.app.models import Rating, RatingSource
    
    user = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254799000104")
    
    # 3 completed deals to bypass threshold
    for i in range(3):
        deal = Deal(seller_id=user.id, item_description=f"Deal {i}", agreed_price=100.0, status=DealStatus.COMPLETED)
        db_session.add(deal)
    db_session.commit()
    
    # Let's get the deal IDs
    deals = db_session.query(Deal).filter(Deal.seller_id == user.id).all()
    
    # Rating 1: Manual positive rating (applied)
    r1 = Rating(deal_id=deals[0].id, ratee_id=user.id, score=1.0, rating_source=RatingSource.MANUAL, is_applied=True)
    db_session.add(r1)
    
    # Rating 2: Dispute outcome positive rating (applied)
    r2 = Rating(deal_id=deals[1].id, ratee_id=user.id, score=1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
    db_session.add(r2)
    
    # Rating 3: Dispute outcome negative rating (applied)
    r3 = Rating(deal_id=deals[2].id, ratee_id=user.id, score=-1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
    db_session.add(r3)
    
    # Rating 4: Manual positive rating (unapplied / pending) -> should be ignored!
    r4 = Rating(deal_id=deals[0].id, ratee_id=user.id, score=1.0, rating_source=RatingSource.MANUAL, is_applied=False)
    db_session.add(r4)
    db_session.commit()
    
    # Total applied ratings: 2 positive, 1 negative = 3 total.
    # Positive rate: 2/3 = 66.67%
    summary = RatingService.get_profile_summary(db_session, user)
    assert summary["positive_rate"] == 66.67

def test_dispute_auto_assignment_and_reminders(db_session):
    """Test staff auto-assignment with load balancing, arbitrator re-roll constraint, and proactive reminders."""
    from backend.app.models import User, UserRole, Dispute, DisputeTier
    from backend.app.services.chat_bot import ChatBotService
    from backend.app.services.scheduler import run_tier_1_checks
    
    # 1. Create two Mediators
    med1 = User(phone_or_handle="MED_TEST_1", role=UserRole.MODERATOR, platform=PlatformType.WHATSAPP)
    med2 = User(phone_or_handle="MED_TEST_2", role=UserRole.MODERATOR, platform=PlatformType.WHATSAPP)
    db_session.add_all([med1, med2])
    db_session.commit()
    
    # Give MED_TEST_1 an open dispute (high load)
    d_load = Dispute(deal_id="deal_load", filed_by="user_x", reason="Load", tier=DisputeTier.TIER_2_AI, human_moderator_id=med1.id)
    db_session.add(d_load)
    db_session.commit()
    
    # 2. Trigger auto-assignment for a new dispute
    d_new = Dispute(deal_id="deal_new", filed_by="user_y", reason="New case", tier=DisputeTier.TIER_2_AI)
    db_session.add(d_new)
    db_session.commit()
    
    ChatBotService.auto_assign_mediator(db_session, d_new)
    
    # It should assign to med2 because med2 has 0 open cases, while med1 has 1 open case
    assert d_new.human_moderator_id == med2.id
    
    # 3. Create two Arbitrators
    arb1 = User(phone_or_handle="ARB_TEST_1", role=UserRole.ARBITRATOR, platform=PlatformType.WHATSAPP)
    arb2 = User(phone_or_handle="ARB_TEST_2", role=UserRole.ARBITRATOR, platform=PlatformType.WHATSAPP)
    db_session.add_all([arb1, arb2])
    db_session.commit()
    
    # Re-roll constraint: if original mediator is ARB_TEST_1 (say mediator.id == arb1.id),
    # then it must assign to arb2.
    d_appeal = Dispute(deal_id="deal_appeal", filed_by="user_z", reason="Appeal case", tier=DisputeTier.TIER_2_AI, human_moderator_id=arb1.id)
    db_session.add(d_appeal)
    db_session.commit()
    
    ChatBotService.auto_assign_arbitrator(db_session, d_appeal)
    
    # It should filter out arb1 (matching human_moderator_id) and assign to arb2
    assert d_appeal.assigned_arbitrator_id == arb2.id
    
    # 4. Proactive reminders verification
    # Setup a dispute that is resolved but within the appeal window
    from datetime import datetime, UTC, timedelta
    d_rem = Dispute(
        deal_id="deal_rem",
        filed_by=med1.id,
        reason="Resolved",
        tier=DisputeTier.TIER_3_HUMAN,
        resolved_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=8), # > 7.5s ago
        appeal_reminder_sent=False
    )
    db_session.add(d_rem)
    db_session.commit()
    
    # Run the scheduler checks
    run_tier_1_checks(db_session)
    db_session.refresh(d_rem)
    
    # The reminder should have fired and marked appeal_reminder_sent = True
    assert d_rem.appeal_reminder_sent is True

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
    ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, seller_phone)
    
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
    ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, seller_phone)
    ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, buyer_phone)
    
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
    ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, seller_phone)
    
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
    from backend.app.models import Dispute, DisputeTier, OutcomeType, User, UserRole, Payment, PaymentStatus
    
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
    
    # 3. Buyer submits dispute reason -> starts response window
    reply_reason = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "The box is empty!")
    assert "recorded" in reply_reason
    
    # Seller submits response to trigger AI analysis
    reply_seller = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "I sent a Sony headphone.")
    assert "recorded and submitted" in reply_seller
    
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
    assert dispute.resolution_statement == "Heuristic matched"
    assert deal.status == DealStatus.DISPUTED

    # 5. Buyer files an APPEAL
    reply_appeal = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "APPEAL")
    assert "dispute appeal request" in reply_appeal.lower()
    
    # Confirm the appeal fee payment trigger
    reply_confirm = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "CONFIRM")
    assert "sent an m-pesa stk push" in reply_confirm.lower()
    
    # Simulate payment callback
    from backend.app.config import settings
    from backend.app.routes.dashboard import simulate_mpesa_payment
    payment = db_session.query(Payment).filter(
        Payment.deal_id == deal.id,
        Payment.status == PaymentStatus.PENDING,
        Payment.amount == settings.ESCALATION_FEE_KES
    ).first()
    assert payment is not None
    simulate_mpesa_payment(checkout_id=payment.stk_push_ref, db=db_session)
    
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

def test_non_filer_appeals_if_lost(db_session):
    """Verify that the party who lost is eligible to appeal (non-filer loses case)."""
    from backend.app.models import User, UserRole, Dispute, DisputeTier, Deal, DealStatus, OutcomeType
    from backend.app.services.chat_bot import ChatBotService
    from datetime import datetime, UTC
    
    # Setup deal
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000001")
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000002")
    deal = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="EliteBook", agreed_price=100.0, status=DealStatus.DISPUTED)
    db_session.add(deal)
    db_session.commit()
    
    # Dispute filed by Buyer (filer = buyer)
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Corrupt file",
        tier=DisputeTier.TIER_3_HUMAN,
        final_outcome=OutcomeType.REFUND, # Fully favors Buyer (filer won, non-filer seller lost)
        resolved_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db_session.add(dispute)
    db_session.commit()
    
    # 1. Non-filer (Seller) appeals -> should be allowed since they lost the case!
    reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000002", "APPEAL")
    assert "Dispute Appeal Request" in reply
    
    # Confirm
    reply_confirm = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000002", "CONFIRM")
    assert "sent an M-Pesa STK Push" in reply_confirm
    
    # Simulate payment
    from backend.app.config import settings
    from backend.app.models import Payment, PaymentStatus
    from backend.app.routes.dashboard import simulate_mpesa_payment
    payment = db_session.query(Payment).filter(
        Payment.deal_id == deal.id,
        Payment.status == PaymentStatus.PENDING,
        Payment.amount == settings.ESCALATION_FEE_KES
    ).first()
    assert payment is not None
    simulate_mpesa_payment(checkout_id=payment.stk_push_ref, db=db_session)
    
    db_session.refresh(dispute)
    assert dispute.is_appeal is True
    
    # Reset is_appeal to test other branches
    dispute.is_appeal = False
    dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.commit()
    
    # 2. Filer (Buyer) won -> if buyer tries to appeal, it should be rejected because they won!
    reply_fail = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000001", "APPEAL")
    assert "Only the party who lost the dispute (Seller) is eligible to appeal" in reply_fail

def test_filer_appeals_if_lost(db_session):
    """Verify that the filer is eligible to appeal if they lost the case."""
    from backend.app.models import User, UserRole, Dispute, DisputeTier, Deal, DealStatus, OutcomeType
    from backend.app.services.chat_bot import ChatBotService
    from datetime import datetime, UTC
    
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000003")
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000004")
    deal = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="EliteBook", agreed_price=100.0, status=DealStatus.DISPUTED)
    db_session.add(deal)
    db_session.commit()
    
    # Dispute filed by Buyer (filer = buyer)
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Corrupt file",
        tier=DisputeTier.TIER_3_HUMAN,
        final_outcome=OutcomeType.RELEASE, # Fully favors Seller (filer lost, non-filer won)
        resolved_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db_session.add(dispute)
    db_session.commit()
    
    # Filer (Buyer) lost -> they should be allowed to appeal!
    reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000003", "APPEAL")
    assert "Dispute Appeal Request" in reply
    
    # Confirm
    reply_confirm = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000003", "CONFIRM")
    assert "sent an M-Pesa STK Push" in reply_confirm
    
    # Simulate payment
    from backend.app.config import settings
    from backend.app.models import Payment, PaymentStatus
    from backend.app.routes.dashboard import simulate_mpesa_payment
    payment = db_session.query(Payment).filter(
        Payment.deal_id == deal.id,
        Payment.status == PaymentStatus.PENDING,
        Payment.amount == settings.ESCALATION_FEE_KES
    ).first()
    assert payment is not None
    simulate_mpesa_payment(checkout_id=payment.stk_push_ref, db=db_session)
    
    db_session.refresh(dispute)
    assert dispute.is_appeal is True

def test_response_window_flow_evidence_before_review(db_session):
    """Verify response window flow: dispute filing notifies non-filer, non-filer submits evidence and response, triggering AI/mediator assignment."""
    from backend.app.models import User, UserRole, Dispute, DisputeTier, Deal, DealStatus, Evidence
    from backend.app.services.chat_bot import ChatBotService, USER_SESSIONS
    
    buyer = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000005")
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254700000006")
    deal = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="Laptop", agreed_price=100.0, status=DealStatus.SHIPPED)
    db_session.add(deal)
    db_session.commit()
    
    # Initialize buyer session
    buyer_sess = USER_SESSIONS.setdefault("254700000005", {"state": "IDLE", "deal_id": deal.id})
    
    # 1. Buyer files dispute
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000005", "NO")
    assert buyer_sess["state"] == "AWAITING_DISPUTE_REASON"
    
    # Buyer submits statement
    reply_filer = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000005", "It was broken.")
    assert "Your dispute has been recorded" in reply_filer
    assert buyer_sess["state"] == "IDLE"
    
    # Verify non-filer (seller) is transitioned to AWAITING_DISPUTE_RESPONSE
    seller_sess = USER_SESSIONS.get("254700000006")
    assert seller_sess is not None
    assert seller_sess["state"] == "AWAITING_DISPUTE_RESPONSE"
    
    dispute = db_session.query(Dispute).filter(Dispute.deal_id == deal.id).first()
    assert dispute is not None
    assert dispute.response_window_deadline is not None
    assert dispute.human_moderator_id is None # Not assigned yet!
    
    # 2. Non-filer (Seller) uploads evidence first (simulated database upload)
    ev = Evidence(deal_id=deal.id, submitted_by=seller.id, file_url="/test/evidence.png")
    db_session.add(ev)
    db_session.commit()
    
    # 3. Non-filer (Seller) submits statement
    reply_nonfiler = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254700000006", "I sent a working device.")
    assert "Your response statement has been recorded" in reply_nonfiler
    assert seller_sess["state"] == "IDLE"
    
    # Verify dispute is updated and mediator is auto-assigned
    db_session.refresh(dispute)
    assert dispute.response_statement == "I sent a working device."
    assert dispute.response_window_deadline is None
    assert dispute.human_moderator_id is not None

def test_photo_reuse_across_deals(db_session):
    """Verify that using the same gallery-captured photo across two different deals triggers the reuse fraud warning."""
    from backend.app.models import User, Deal, DealStatus, CapturedPhoto, Evidence, Dispute, DisputeTier
    from backend.app.routes.dashboard import upload_simulated_evidence
    from backend.app.services.ai_service import AIService
    from datetime import datetime, UTC
    
    # 1. Setup Deal 1 & Deal 2
    seller = User(phone_or_handle="254799000001", platform="whatsapp")
    buyer = User(phone_or_handle="254799000002", platform="whatsapp")
    db_session.add_all([seller, buyer])
    db_session.commit()
    
    deal1 = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="Device A", agreed_price=100.0, status=DealStatus.FUNDED, verification_code="HU-1111")
    deal2 = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="Device B", agreed_price=200.0, status=DealStatus.FUNDED, verification_code="HU-2222")
    db_session.add_all([deal1, deal2])
    db_session.commit()
    
    # 2. Capture a photo to the gallery
    photo = CapturedPhoto(
        user_id=seller.id,
        file_url="/simulated/media/HU-1111_package.jpg",
        gps_location="-1.2921, 36.8219",
        perceptual_hash="1f2f3f4f5f6f7f8f",
        captured_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db_session.add(photo)
    db_session.commit()
    
    # 3. Submit as evidence for Deal 1 (gallery-selected)
    upload_simulated_evidence(deal_id=deal1.id, sender_id=seller.id, gallery_photo_id=photo.id, db=db_session)
    
    # 4. Submit the SAME photo as evidence for Deal 2 (gallery-selected)
    upload_simulated_evidence(deal_id=deal2.id, sender_id=seller.id, gallery_photo_id=photo.id, db=db_session)
    
    # 5. Verify that Deal 2's evidence is logged with the same hash
    evs = db_session.query(Evidence).filter(Evidence.deal_id == deal2.id).all()
    assert len(evs) == 1
    assert evs[0].perceptual_hash == "1f2f3f4f5f6f7f8f"
    
    # 6. File dispute for Deal 2 and run AI Moderation to trigger the reuse check
    dispute = Dispute(deal_id=deal2.id, filed_by=buyer.id, reason="Item never arrived", tier=DisputeTier.TIER_2_AI)
    db_session.add(dispute)
    db_session.commit()
    
    AIService.run_moderation(db_session, dispute.id)
    
    # Refresh evidence and check for the WARNING tag in the hash!
    db_session.refresh(evs[0])
    assert "WARNING" in evs[0].perceptual_hash
    assert "Reused!" in evs[0].perceptual_hash

def test_gallery_old_photo_code_mismatch(db_session):
    """Verify that a gallery-selected photo submitted for a deal requires the current verification code to pass verification checks."""
    from backend.app.models import User, Deal, DealStatus, CapturedPhoto, Evidence
    from backend.app.routes.dashboard import upload_simulated_evidence
    from datetime import datetime, UTC
    
    seller = User(phone_or_handle="254799000003", platform="whatsapp")
    buyer = User(phone_or_handle="254799000004", platform="whatsapp")
    db_session.add_all([seller, buyer])
    db_session.commit()
    
    # Deal A requires "HU-AAAA", Deal B requires "HU-BBBB"
    deal_a = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="Laptop A", agreed_price=100.0, status=DealStatus.FUNDED, verification_code="HU-AAAA")
    deal_b = Deal(seller_id=seller.id, buyer_id=buyer.id, item_description="Laptop B", agreed_price=200.0, status=DealStatus.FUNDED, verification_code="HU-BBBB")
    db_session.add_all([deal_a, deal_b])
    db_session.commit()
    
    # 1. Capture a photo matching Deal A's code to the gallery
    photo = CapturedPhoto(
        user_id=seller.id,
        file_url="/simulated/media/HU-AAAA_package.jpg",
        gps_location="-1.2921, 36.8219",
        perceptual_hash="hash_unique_1111",
        captured_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db_session.add(photo)
    db_session.commit()
    
    # 2. Submit for Deal B (which requires "HU-BBBB") -> should fail code visibility check!
    upload_simulated_evidence(deal_id=deal_b.id, sender_id=seller.id, gallery_photo_id=photo.id, db=db_session)
    
    evs_b = db_session.query(Evidence).filter(Evidence.deal_id == deal_b.id).all()
    assert len(evs_b) == 1
    assert evs_b[0].dynamic_code_detected is False
    
    # 3. Submit for Deal A (which requires "HU-AAAA") -> should succeed code visibility check!
    upload_simulated_evidence(deal_id=deal_a.id, sender_id=seller.id, gallery_photo_id=photo.id, db=db_session)
    
    evs_a = db_session.query(Evidence).filter(Evidence.deal_id == deal_a.id).all()
    assert len(evs_a) == 1
    assert evs_a[0].dynamic_code_detected is True

def test_first_time_user_onboarding_and_resume(db_session):
    """Verify first-time user Flow onboarding, fallback, and automatic resume of the pending action."""
    from backend.app.models import User, PlatformType
    
    new_phone = "254799888888"
    
    # 1. User messages SELL for the first time on WhatsApp
    r1 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "SELL")
    assert "profile setup" in r1.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FLOW"
    assert USER_SESSIONS[new_phone]["onboarding_pending_action"] == "SELL"
    
    # Verify user record is NOT created in DB yet
    user_db = db_session.query(User).filter(User.phone_or_handle == new_phone).first()
    assert user_db is None
    
    # 2. Simulate user typing a command or name (START) to switch to text fallback
    r2 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "START")
    assert "full name" in r2.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FALLBACK_NAME"
    
    # 3. Enter Name
    r3 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "Allan Kip")
    assert "m-pesa" in r3.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FALLBACK_PAYOUT"
    assert USER_SESSIONS[new_phone]["onboarding_data"]["name"] == "Allan Kip"
    
    # 4. Enter Payout Number
    r3_payout = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "254711111111")
    assert "backup email" in r3_payout.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FALLBACK_RECOVERY"
    assert USER_SESSIONS[new_phone]["onboarding_data"]["payout_mpesa_number"] == "254711111111"

    # 5. Enter Recovery
    r4 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "allan@example.com")
    assert "location" in r4.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FALLBACK_LOCATION"
    
    # 6. Skip Location
    r5 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "skip")
    assert "consent" in r5.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FALLBACK_CONSENT"
    assert USER_SESSIONS[new_phone]["onboarding_data"]["location"] is None
    
    # 7. Agree to consent and assert original 'SELL' action is resumed!
    r6 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, "YES")
    assert "item description" in r6.lower() # Verify it welcomes user and prompts for description (resuming 'SELL')
    
    # Verify user is created in database with the fields
    user_db = db_session.query(User).filter(User.phone_or_handle == new_phone).first()
    assert user_db is not None
    assert user_db.name == "Allan Kip"
    assert user_db.payout_mpesa_number == "254711111111"
    assert user_db.recovery_email_or_phone == "allan@example.com"
    assert user_db.location is None
    assert user_db.consent_accepted_at is not None
    
    # Verify session is cleaned up and state is now description prompt
    assert USER_SESSIONS[new_phone]["state"] == "AWAITING_DESC"

def test_first_time_user_flow_json_submission(db_session):
    """Verify first-time user Flow onboarding via simulated JSON payload and action resume."""
    from backend.app.models import User, PlatformType, Deal
    
    new_phone = "254799777777"
    
    # 1. User messages a JOIN code for the first time
    # Set up a deal first
    seller = ChatBotService.get_or_create_user(db_session, PlatformType.WHATSAPP, "254711111111")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254711111111", "SELL")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254711111111", "iPhone X")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254711111111", "30000")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, "254711111111", "3")
    deal = db_session.query(Deal).filter(Deal.item_description == "iPhone X").first()
    
    join_cmd = f"JOIN_{deal.id}"
    
    r1 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, join_cmd)
    assert "profile setup" in r1.lower()
    assert USER_SESSIONS[new_phone]["state"] == "ONBOARDING_FLOW"
    assert USER_SESSIONS[new_phone]["onboarding_pending_action"] == join_cmd
    
    # 2. Submit simulated Flow JSON
    import json
    flow_payload = {
        "flow_name": "profile_creation",
        "name": "Allan Kipkor",
        "payout_mpesa_number": "254712345678",
        "recovery_contact": "allankip@example.com",
        "location": "Nairobi"
    }
    r2 = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, new_phone, json.dumps(flow_payload))
    assert "welcome" in r2.lower()
    assert "joining a secure" in r2.lower() # Verify it resumed the JOIN action
    
    # Verify user record
    user_db = db_session.query(User).filter(User.phone_or_handle == new_phone).first()
    assert user_db is not None
    assert user_db.name == "Allan Kipkor"
    assert user_db.payout_mpesa_number == "254712345678"
    assert user_db.recovery_email_or_phone == "allankip@example.com"
    assert user_db.location == "Nairobi"

def test_mediator_save_resolution_and_scheduler_lapsed_payout(db_session):
    """Verify separate save resolution (24h appeal window notification) and lapsed payout scheduler logic."""
    from backend.app.models import User, UserRole, Deal, DealStatus, Dispute, DisputeTier, OutcomeType, PlatformType, Payment, PaymentStatus, ChatLog
    from backend.app.routes.dashboard import resolve_dispute_manually
    from backend.app.services.scheduler import run_tier_1_checks
    from datetime import datetime, UTC, timedelta
    
    # 1. Setup users, deal, and dispute
    seller = User(phone_or_handle="254711111199", role=UserRole.USER, platform=PlatformType.WHATSAPP, trust_score=100.0)
    buyer = User(phone_or_handle="254722222299", role=UserRole.USER, platform=PlatformType.WHATSAPP, trust_score=100.0)
    mediator = User(phone_or_handle="MOD_TEST_M1", role=UserRole.MODERATOR, platform=PlatformType.WHATSAPP)
    db_session.add_all([seller, buyer, mediator])
    db_session.commit()
    
    deal = Deal(
        seller_id=seller.id,
        buyer_id=buyer.id,
        item_description="Test Laptop",
        agreed_price=1000.0,
        status=DealStatus.DISPUTED
    )
    db_session.add(deal)
    db_session.commit()
    
    dispute = Dispute(
        deal_id=deal.id,
        filed_by=buyer.id,
        reason="Did not boot",
        tier=DisputeTier.TIER_2_AI,
        is_appeal=False
    )
    db_session.add(dispute)
    db_session.commit()
    
    # Add a funded payment
    p = Payment(deal_id=deal.id, stk_push_ref="payout_ref_test", amount=1000.0, status=PaymentStatus.PAID)
    db_session.add(p)
    db_session.commit()
    
    # 2. Mediator resolves dispute manually (first-instance)
    res = resolve_dispute_manually(
        dispute_id=dispute.id,
        outcome="refund",
        reasoning="Seller provided no valid counter-evidence",
        resolver_id=mediator.id,
        db=db_session
    )
    
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # Assertions for separate save and 24h notifications
    assert res["status"] == "resolved"
    assert dispute.resolved_at is not None
    assert dispute.final_outcome == OutcomeType.REFUND
    assert dispute.resolution_statement == "Seller provided no valid counter-evidence"
    assert dispute.is_appeal is False
    assert deal.status == DealStatus.DISPUTED  # Payout did NOT trigger immediately!
    
    # Verify both parties received WhatsApp messages with 24 hours instructions logged in ChatLog
    sent_messages = db_session.query(ChatLog).filter(ChatLog.deal_id == deal.id).all()
    assert len(sent_messages) >= 2
    
    msg_contents = [m.message_content for m in sent_messages]
    assert any("24 hours" in content and "Seller provided no valid counter-evidence" in content for content in msg_contents)

    # 3. Verify scheduler triggers payout after window lapses
    # Simulate time passing beyond the appeal window (simulation mode uses 15 seconds)
    dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=20)
    db_session.commit()
    
    run_tier_1_checks(db_session)
    db_session.refresh(deal)
    db_session.refresh(dispute)
    
    # After scheduler run: payout triggered, status updated to REFUNDED
    assert deal.status == DealStatus.REFUNDED
    
    # Verify payout record exists
    payment = db_session.query(Payment).filter(Payment.deal_id == deal.id, Payment.b2c_payout_ref != None).first()
    assert payment is not None
    assert payment.status == PaymentStatus.REFUND_COMPLETED


def test_staff_portal_authentication_and_scoping(db_session):
    """Test staff authentication login, logout, and role-scoping behavior."""
    from backend.app.routes.dashboard import login_staff, get_disputes_queue
    from backend.app.models import User, UserRole, Dispute, Deal, DealStatus
    
    # 1. Seed staff users in test DB
    import hashlib
    from backend.app.models import PlatformType
    mod = User(phone_or_handle="MOD_1", role=UserRole.MODERATOR, platform=PlatformType.WHATSAPP, password_hash=hashlib.sha256(b"moderator123").hexdigest())
    arb = User(phone_or_handle="ARB_1", role=UserRole.ARBITRATOR, platform=PlatformType.WHATSAPP, password_hash=hashlib.sha256(b"arbitrator123").hexdigest())
    admin = User(phone_or_handle="ADMIN_1", role=UserRole.ADMIN, platform=PlatformType.WHATSAPP, password_hash=hashlib.sha256(b"admin123").hexdigest())
    db_session.add_all([mod, arb, admin])
    db_session.commit()
    
    # 2. Login as moderator
    login_res = login_staff(username="MOD_1", password="moderator123", db=db_session)
    assert "token" in login_res
    assert login_res["user"]["phone_or_handle"] == "MOD_1"
    assert login_res["user"]["role"] == "moderator"
    
    token = login_res["token"]
    db_session.refresh(mod)
    assert mod.session_token == token
    
    # 3. Test get_disputes_queue with moderator (should filter by MOD_1 and is_appeal=False)
    deal1 = Deal(seller_id="s1", buyer_id="b1", item_description="Test Item 1", agreed_price=1000, status=DealStatus.DISPUTED)
    deal2 = Deal(seller_id="s2", buyer_id="b2", item_description="Test Item 2", agreed_price=2000, status=DealStatus.DISPUTED)
    db_session.add_all([deal1, deal2])
    db_session.commit()
    
    disp1 = Dispute(deal_id=deal1.id, filed_by="b1", reason="damaged", is_appeal=False, human_moderator_id=mod.id)
    disp2 = Dispute(deal_id=deal2.id, filed_by="b2", reason="appeal", is_appeal=True, assigned_arbitrator_id=arb.id)
    db_session.add_all([disp1, disp2])
    db_session.commit()
    
    # Run queue for moderator
    mod_queue = get_disputes_queue(current_user=mod, db=db_session)
    # Moderator should only see disp1
    assert len(mod_queue) == 1
    assert mod_queue[0]["id"] == disp1.id
    
    # Run queue for arbitrator
    arb_queue = get_disputes_queue(current_user=arb, db=db_session)
    # Arbitrator should only see disp2
    assert len(arb_queue) == 1
    assert arb_queue[0]["id"] == disp2.id
    
    # Run queue for admin
    admin_queue = get_disputes_queue(current_user=admin, db=db_session)
    # Admin should see both
    assert len(admin_queue) == 2



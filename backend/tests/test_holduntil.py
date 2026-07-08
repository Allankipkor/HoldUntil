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

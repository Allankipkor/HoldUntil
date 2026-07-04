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
    assert "consent" in join_reply.lower()
    assert "confirm" in join_reply.lower()
    
    # Reload deal from DB
    db_session.refresh(deal)
    assert deal.buyer_id is not None
    assert deal.status == DealStatus.AWAITING_CONFIRMATION

    # Buyer confirms terms
    confirm_reply = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "CONFIRM")
    assert "m-pesa" in confirm_reply.lower() or "stk" in confirm_reply.lower()

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
    """Test full dispute flow, including AI moderation, satisfaction confirmation, filer-restricted escalation, and trust score/win rate updates."""
    from backend.app.models import Dispute, DisputeTier, OutcomeType
    
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
    # Seller creates
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SELL")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "Sony WH-1000XM5")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "30000")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "3")
    
    deal = db_session.query(Deal).filter(Deal.item_description == "Sony WH-1000XM5").first()
    assert deal is not None
    
    # Buyer joins & confirms
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, f"JOIN_{deal.id}")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "CONFIRM")
    
    # Force payment confirmation to set status = FUNDED
    from backend.app.routes.dashboard import simulate_mpesa_payment
    # Create mock payment to simulate it
    p = Payment(deal_id=deal.id, stk_push_ref="test_ref", amount=30000.0, status=PaymentStatus.PENDING)
    db_session.add(p)
    db_session.commit()
    
    simulate_mpesa_payment(checkout_id="test_ref", db=db_session)
    db_session.refresh(deal)
    assert deal.status == DealStatus.FUNDED
    
    # Seller ships (which updates status = SHIPPED)
    from backend.app.routes.dashboard import upload_simulated_evidence
    upload_simulated_evidence(deal_id=deal.id, sender_id=seller.id, photo_name="package_with_code.jpg", db=db_session)
    db_session.refresh(deal)
    assert deal.status == DealStatus.SHIPPED
    
    # 2. Buyer disputes the deal by sending NO (updates status = DISPUTED)
    reply_no = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "NO")
    assert "WARNING" in reply_no
    assert "dispute" in reply_no.lower()
    
    db_session.refresh(deal)
    assert deal.status == DealStatus.DISPUTED
    
    # A dispute record should have been created in the database
    dispute = db_session.query(Dispute).filter(Dispute.deal_id == deal.id).first()
    assert dispute is not None
    assert dispute.tier == DisputeTier.TIER_2_AI
    assert dispute.filed_by == buyer.id
    
    # 3. Buyer submits dispute reason -> triggers AI Moderator instantly
    reply_reason = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "The box is empty!")
    assert "recorded" in reply_reason.lower()
    
    db_session.refresh(dispute)
    db_session.refresh(deal)
    
    # Ensure AI moderator executed without crashing (DarajaService NameError fix verification)
    assert dispute.resolved_at is not None
    assert dispute.ai_decision is not None
    
    # In heuristics fallback, since seller uploaded evidence (package_with_code.jpg has verified code),
    # the fallback engine should rule in favor of the seller: RELEASE
    assert dispute.final_outcome == OutcomeType.RELEASE
    assert deal.status == DealStatus.COMPLETED
    
    # Check that trust scores were adjusted:
    # Seller should be rewarded (+2.0 points -> clamped to 100.0)
    # Buyer (filer of meritless dispute) should be penalized (-15.0 points -> 85.0)
    db_session.refresh(seller)
    db_session.refresh(buyer)
    assert seller.trust_score == 100.0
    assert buyer.trust_score == 85.0
    
    # Check that win rates were updated
    assert seller.dispute_win_rate == 1.0 # seller won
    assert buyer.dispute_win_rate == 0.0 # buyer lost
    
    # 4. Escalate command validation
    # Seller trying to escalate (not the filer) should be blocked
    escalate_seller = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "ESCALATE")
    assert "Only the user who raised the dispute" in escalate_seller
    
    # 5. Satisfaction flow validation
    # Seller trying to confirm satisfaction (not the filer) should be blocked
    satisfied_seller = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "SATISFIED")
    assert "Only the user who raised the dispute" in satisfied_seller
    
    # Buyer (filer) confirms satisfaction
    satisfied_buyer = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "SATISFIED")
    assert "officially closed" in satisfied_buyer.lower()
    
    db_session.refresh(dispute)
    assert dispute.filer_satisfied is True
    
    # 6. Escalate after satisfied validation
    # Buyer trying to escalate after marking satisfied should be blocked
    escalate_after_satisfied = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, buyer_phone, "ESCALATE")
    assert "accepted and closed" in escalate_after_satisfied.lower()

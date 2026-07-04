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
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "230")
    ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "3")
    
    deal = db_session.query(Deal).filter(Deal.item_description == "iPhone 17 Pro").first()
    assert deal.agreed_price == 230.0
    
    # 2. Seller corrects the price to 230000
    r_price = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "PRICE 230000")
    db_session.refresh(deal)
    assert deal.agreed_price == 230000.0
    assert "230000" in r_price
    
    # 3. Seller resets/cancels the deal
    r_cancel = ChatBotService.process_message(db_session, PlatformType.WHATSAPP, seller_phone, "CANCEL")
    db_session.refresh(deal)
    assert deal.status == DealStatus.CANCELLED
    assert "cancelled" in r_cancel.lower()
    assert USER_SESSIONS[seller_phone]["state"] == "IDLE"

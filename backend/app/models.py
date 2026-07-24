import uuid
from datetime import datetime, UTC
from enum import Enum

def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Text, JSON, Enum as SQLEnum
from sqlalchemy.orm import relationship
from backend.app.database import Base

class PlatformType(str, Enum):
    WHATSAPP = "whatsapp"
    MESSENGER = "messenger"
    INSTAGRAM = "instagram"

class UserRole(str, Enum):
    USER = "user"
    MODERATOR = "moderator"
    ARBITRATOR = "arbitrator"
    ADMIN = "admin"

class DealType(str, Enum):
    DIGITAL = "digital"
    SHIPPED = "shipped"
    HANDOFF = "handoff"
    REMOTE_SERVICE = "remote_service"

class DealStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FUNDED = "funded"
    SHIPPED = "shipped"
    DELIVERY_PROMPTED = "delivery_prompted"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"

class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    PAYOUT_PROCESSING = "payout_processing"
    PAYOUT_COMPLETED = "payout_completed"
    REFUND_PROCESSING = "refund_processing"
    REFUND_COMPLETED = "refund_completed"
    FAILED = "failed"

class DisputeTier(str, Enum):
    TIER_1_AUTO = "tier_1_auto"
    TIER_2_AI = "tier_2_ai"
    TIER_3_HUMAN = "tier_3_human"

class OutcomeType(str, Enum):
    RELEASE = "release"
    REFUND = "refund"
    PARTIAL_SPLIT = "partial_split"

class RatingSource(str, Enum):
    MANUAL = "manual"
    DISPUTE_OUTCOME = "dispute_outcome"

class ResolutionMethod(str, Enum):
    SELF_RELEASE = "self_release"
    SELF_REFUND = "self_refund"
    HUMAN_FIRST_INSTANCE = "human_first_instance"
    APPEAL = "appeal"

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    platform = Column(SQLEnum(PlatformType), nullable=False)
    phone_or_handle = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)
    recovery_email_or_phone = Column(String(100), nullable=True)
    location = Column(String(100), nullable=True)
    consent_accepted_at = Column(DateTime, nullable=True)
    payout_mpesa_number = Column(String(20), nullable=True)
    trust_score = Column(Float, default=100.0)
    dispute_win_rate = Column(Float, default=0.0)
    ai_overturn_flag_count = Column(Integer, default=0)
    role = Column(SQLEnum(UserRole), default=UserRole.USER, nullable=False)
    password_hash = Column(String(100), nullable=True)
    session_token = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    ratings_received = relationship("Rating", foreign_keys="Rating.ratee_id", back_populates="ratee")
    deals_as_seller = relationship("Deal", foreign_keys="Deal.seller_id", back_populates="seller")
    deals_as_buyer = relationship("Deal", foreign_keys="Deal.buyer_id", back_populates="buyer")
    disputes_filed = relationship("Dispute", foreign_keys="Dispute.filed_by", back_populates="filer")
    evidence_submitted = relationship("Evidence", back_populates="submitter")
    captured_photos = relationship("CapturedPhoto", back_populates="user", cascade="all, delete-orphan")

class Deal(Base):
    __tablename__ = "deals"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    seller_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    buyer_id = Column(String(36), ForeignKey("users.id"), nullable=True)  # Nullable until buyer accepts invite
    item_description = Column(Text, nullable=False)
    agreed_price = Column(Float, nullable=False)
    delivery_deadline = Column(DateTime, nullable=True)
    status = Column(SQLEnum(DealStatus), default=DealStatus.DRAFT, nullable=False, index=True)
    verification_code = Column(String(20), nullable=True)
    courier_name = Column(String(50), nullable=True)
    tracking_number = Column(String(100), nullable=True)
    seller_confirmed = Column(Boolean, default=False)
    buyer_confirmed = Column(Boolean, default=False)
    deal_type = Column(SQLEnum(DealType), default=DealType.SHIPPED, nullable=False)
    transaction_type = Column(String(50), nullable=True)
    seller_disclaimer_acknowledged = Column(Boolean, default=False, nullable=False)
    buyer_disclaimer_acknowledged = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    seller = relationship("User", foreign_keys=[seller_id], back_populates="deals_as_seller")
    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="deals_as_buyer")
    payments = relationship("Payment", back_populates="deal", cascade="all, delete-orphan")
    disputes = relationship("Dispute", back_populates="deal", cascade="all, delete-orphan")
    evidence = relationship("Evidence", back_populates="deal", cascade="all, delete-orphan")
    chat_logs = relationship("ChatLog", back_populates="deal", cascade="all, delete-orphan")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    stk_push_ref = Column(String(100), nullable=True, index=True)
    c2b_confirmation_ref = Column(String(100), nullable=True, index=True)
    b2c_payout_ref = Column(String(100), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    deal = relationship("Deal", back_populates="payments")

class Dispute(Base):
    __tablename__ = "disputes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    filed_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    tier = Column(SQLEnum(DisputeTier), default=DisputeTier.TIER_2_AI, nullable=False, index=True)
    
    # Tier 2 AI decision metrics
    ai_decision = Column(SQLEnum(OutcomeType), nullable=True)
    ai_reasoning = Column(Text, nullable=True)
    ai_confidence = Column(Float, nullable=True)
    
    # Tier 3 human review metrics
    human_moderator_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    escalation_requested_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    escalation_fee_paid = Column(Boolean, default=False)
    
    # First-instance tracking (preserved after appeal)
    first_instance_outcome = Column(SQLEnum(OutcomeType), nullable=True)
    first_instance_statement = Column(Text, nullable=True)

    # Final Outcome (determined by AI or Human moderator)
    final_outcome = Column(SQLEnum(OutcomeType), nullable=True)
    resolution_method = Column(SQLEnum(ResolutionMethod), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_statement = Column(Text, nullable=True)
    filer_satisfied = Column(Boolean, nullable=True, default=None)
    partial_split_percentage = Column(Integer, nullable=True)
    
    # Appeal tracking
    is_appeal = Column(Boolean, default=False, nullable=False)
    appeal_requested_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    appeal_resolved_at = Column(DateTime, nullable=True)
    appeal_fee_refunded = Column(Boolean, default=False, nullable=False)
    appeal_fee_refund_status = Column(String(20), default="none", nullable=False)
    appeal_fee_payout_ref = Column(String(100), nullable=True, index=True)
    assigned_arbitrator_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    appeal_reminder_sent = Column(Boolean, default=False, nullable=False)
    response_statement = Column(Text, nullable=True)
    response_window_deadline = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    deal = relationship("Deal", back_populates="disputes")
    filer = relationship("User", foreign_keys=[filed_by], back_populates="disputes_filed")
    moderator = relationship("User", foreign_keys=[human_moderator_id])

class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    submitted_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    file_url = Column(String(500), nullable=False)
    perceptual_hash = Column(String(100), nullable=True, index=True)
    exif_data = Column(JSON, nullable=True)
    dynamic_code_detected = Column(Boolean, default=False)
    courier_verified = Column(Boolean, default=False)
    in_app_captured = Column(Boolean, default=False, nullable=False)
    captured_timestamp = Column(DateTime, nullable=True)
    gps_latitude = Column(Float, nullable=True)
    gps_longitude = Column(Float, nullable=True)
    deliverable_file_url = Column(String(500), nullable=True)
    video_call_log = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    deal = relationship("Deal", back_populates="evidence")
    submitter = relationship("User", back_populates="evidence_submitted")

class ChatLog(Base):
    __tablename__ = "chat_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    sender_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    message_content = Column(Text, nullable=False)
    media_url = Column(String(500), nullable=True)
    is_revoked = Column(Boolean, default=False, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    timestamp = Column(DateTime, default=utcnow)

    # Relationships
    deal = relationship("Deal", back_populates="chat_logs")
    sender = relationship("User", foreign_keys=[sender_id])

class Rating(Base):
    __tablename__ = "ratings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    rater_id = Column(String(36), ForeignKey("users.id"), nullable=True)  # Nullable for dispute_outcome
    ratee_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    score = Column(Float, nullable=False)  # 1.0 = thumbs up, -1.0 = thumbs down, 0.0 = neutral/split
    rating_source = Column(SQLEnum(RatingSource), default=RatingSource.MANUAL, nullable=False)
    is_applied = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    deal = relationship("Deal")
    rater = relationship("User", foreign_keys=[rater_id])
    ratee = relationship("User", foreign_keys=[ratee_id], back_populates="ratings_received")

class CapturedPhoto(Base):
    __tablename__ = "captured_photos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    file_url = Column(String(500), nullable=False)
    captured_at = Column(DateTime, default=utcnow)
    gps_location = Column(String(100), nullable=True)
    perceptual_hash = Column(String(100), nullable=True, index=True)

    # Relationships
    user = relationship("User", back_populates="captured_photos")

class BotMessageLog(Base):
    __tablename__ = "bot_message_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient_phone = Column(String(100), index=True, nullable=False)
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=True)
    message_content = Column(Text, nullable=True)
    is_urgent = Column(Boolean, default=False, nullable=False)
    is_direct_reply = Column(Boolean, default=False, nullable=False)
    timestamp = Column(DateTime, default=utcnow, nullable=False)

    deal = relationship("Deal")

class ReminderTracker(Base):
    __tablename__ = "reminder_trackers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    deal_id = Column(String(36), ForeignKey("deals.id"), nullable=False)
    recipient_phone = Column(String(100), nullable=False, index=True)
    pending_action = Column(String(50), nullable=False)  # "confirm_receipt", "rate_deal", "respond_dispute", "appeal_decision"
    reminder_count = Column(Integer, default=0, nullable=False)
    last_sent_at = Column(DateTime, default=utcnow, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    deal = relationship("Deal")


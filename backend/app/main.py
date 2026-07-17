from fastapi import FastAPI, Depends, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.database import engine, Base, SessionLocal, get_db
from backend.app.routes import meta_webhook, daraja_webhook, dashboard
from backend.app.services.scheduler import start_scheduler
from backend.app.services.chat_bot import ChatBotService, USER_SESSIONS
from backend.app.models import PlatformType, User, Deal, DealStatus
import logging
import time

LAST_PROCESSED_MESSAGES = {}

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables in SQLite
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    
    # Auto-migrate SQLite users table for new fields
    try:
        from sqlalchemy import inspect
        from sqlalchemy import text
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('users')]
        with engine.begin() as conn:
            if 'name' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN name VARCHAR(100)"))
                logger.info("Added name column to users table.")
            if 'recovery_email_or_phone' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN recovery_email_or_phone VARCHAR(100)"))
                logger.info("Added recovery_email_or_phone column to users table.")
            if 'location' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN location VARCHAR(100)"))
                logger.info("Added location column to users table.")
            if 'consent_accepted_at' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN consent_accepted_at DATETIME"))
                logger.info("Added consent_accepted_at column to users table.")
            if 'payout_mpesa_number' not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN payout_mpesa_number VARCHAR(20)"))
                logger.info("Added payout_mpesa_number column to users table.")
            
            # Auto-migrate SQLite disputes table for new fields
            columns_disputes = [col['name'] for col in inspector.get_columns('disputes')]
            if 'resolution_statement' not in columns_disputes:
                conn.execute(text("ALTER TABLE disputes ADD COLUMN resolution_statement TEXT"))
                logger.info("Added resolution_statement column to disputes table.")
    except Exception as migration_err:
        logger.error(f"Error running database table migration: {migration_err}")
    
    # Seed staff users
    db = SessionLocal()
    try:
        from backend.app.models import User, UserRole
        staff = [
            ("MOD_1", UserRole.MODERATOR),
            ("ARB_1", UserRole.ARBITRATOR),
            ("ADMIN_1", UserRole.ADMIN)
        ]
        for phone, role in staff:
            exists = db.query(User).filter(User.phone_or_handle == phone).first()
            if not exists:
                user = User(
                    phone_or_handle=phone,
                    role=role,
                    platform=PlatformType.WHATSAPP,
                    trust_score=100.0
                )
                db.add(user)
                logger.info(f"Seeded staff user: {phone} as {role.value}")
        db.commit()
    except Exception as seed_err:
        logger.error(f"Error seeding staff users: {seed_err}")
    finally:
        db.close()
        
    # Start APScheduler tasks
    logger.info("Initializing background scheduler...")
    start_scheduler(SessionLocal)
    
    yield
    # Shutdown logic (if any)
    logger.info("Shutting down services...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Secure Chat-Native Escrow Backend",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Open for ease of testing in Vite/React
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach Webhook and Dashboard Routers
app.include_router(meta_webhook.router, prefix="/api")
app.include_router(daraja_webhook.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")

@app.get("/")
def read_root():
    return {
        "status": "online",
        "app": settings.PROJECT_NAME,
        "mode": "simulation" if settings.SIMULATION_MODE else "production",
        "timestamp": lifespan
    }

# ----------------------------------------------------
# DIRECT SANDBOX CHAT INTERACTION ENDPOINT
# ----------------------------------------------------

@app.post("/api/dialogue")
def dialogue_chat_simulator(
    phone_or_handle: str = Form(...),
    message: str = Form(...),
    platform: str = Form("whatsapp"),
    media_url: str = Form(None),
    db: Session = Depends(get_db)
):
    """
    Direct endpoint for Sandbox simulator.
    Receives chat messages directly from the React interface, Feeds it to the bot,
    and returns the bot's raw response immediately (in addition to logging).
    """
    # 1. Idempotency safety net (2-second window)
    now = time.time()
    last_msg = LAST_PROCESSED_MESSAGES.get(phone_or_handle)
    if last_msg:
        last_time, last_text, last_reply_data = last_msg
        if now - last_time < 2.0 and last_text == message:
            logger.info(f"Duplicate message '{message}' ignored (idempotency key match for {phone_or_handle})")
            return last_reply_data

    try:
        plat_enum = PlatformType(platform.lower())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid platform. Must be whatsapp, messenger, or instagram.")

    # 2. Process message exactly ONCE
    bot_reply = ChatBotService.process_message(
        db=db,
        platform=plat_enum,
        phone_or_handle=phone_or_handle,
        text=message,
        media_url=media_url
    )

    # 3. Retrieve active deal_id from session *after* execution
    session_after = USER_SESSIONS.get(phone_or_handle, {})
    deal_id = session_after.get("deal_id")
    if not deal_id:
        user_db = db.query(User).filter(User.phone_or_handle == phone_or_handle).first()
        if user_db:
            latest_deal = db.query(Deal).filter(
                (Deal.seller_id == user_db.id) | (Deal.buyer_id == user_db.id)
            ).order_by(Deal.created_at.desc()).first()
            if latest_deal and latest_deal.status not in [DealStatus.COMPLETED, DealStatus.REFUNDED, DealStatus.CANCELLED]:
                deal_id = latest_deal.id

    # Save Bot reply to chat logs using the computed deal_id
    if deal_id and bot_reply:
        from datetime import datetime, UTC
        from backend.app.models import ChatLog
        from backend.app.services.meta_service import MetaService
        
        bot_id = MetaService._ensure_bot_user_exists(db)
        
        # Check if the last log is from the bot and matches content to avoid duplicates
        last_log = db.query(ChatLog).filter(ChatLog.deal_id == deal_id).order_by(ChatLog.timestamp.desc()).first()
        if not last_log or last_log.message_content != bot_reply:
            chat_log = ChatLog(
                deal_id=deal_id,
                sender_id=bot_id,
                message_content=bot_reply,
                timestamp=datetime.now(UTC).replace(tzinfo=None)
            )
            db.add(chat_log)
            db.commit()
    
    reply_data = {
        "user": phone_or_handle,
        "message": message,
        "reply": bot_reply,
        "session_state": USER_SESSIONS.get(phone_or_handle, {}).get("state"),
        "deal_id": USER_SESSIONS.get(phone_or_handle, {}).get("deal_id")
    }
    
    # Cache for idempotency checks
    LAST_PROCESSED_MESSAGES[phone_or_handle] = (now, message, reply_data)
    
    return reply_data

@app.get("/api/users/{user_identifier}/profile")
def get_user_public_profile(user_identifier: str, db: Session = Depends(get_db)):
    """
    Expose public profile details (handle, stats, badge) by user ID or phone/handle.
    """
    from backend.app.services.rating_service import RatingService
    
    user = db.query(User).filter(User.id == user_identifier).first()
    if not user:
        user = db.query(User).filter(User.phone_or_handle == user_identifier).first()
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    summary = RatingService.get_profile_summary(db, user)
    return summary

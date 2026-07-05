from fastapi import FastAPI, Depends, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.database import engine, Base, SessionLocal, get_db
from backend.app.routes import meta_webhook, daraja_webhook, dashboard
from backend.app.services.scheduler import start_scheduler
from backend.app.services.chat_bot import ChatBotService, USER_SESSIONS
from backend.app.models import PlatformType
import logging

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
    try:
        plat_enum = PlatformType(platform.lower())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid platform. Must be whatsapp, messenger, or instagram.")

    bot_reply = ChatBotService.process_message(
        db=db,
        platform=plat_enum,
        phone_or_handle=phone_or_handle,
        text=message,
        media_url=media_url
    )

    # Save Bot reply to chat logs so simulator can fetch it
    # Note: ChatBotService already logs the reply if a deal is in context,
    # but we will return it directly to the dashboard caller for instant rendering.
    session = USER_SESSIONS.get(phone_or_handle, {})
    
    return {
        "user": phone_or_handle,
        "message": message,
        "reply": bot_reply,
        "session_state": session.get("state"),
        "deal_id": session.get("deal_id")
    }

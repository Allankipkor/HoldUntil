from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "HoldUntil"
    DEBUG: bool = True
    SIMULATION_MODE: bool = True  # True enables mock payments & messages

    # Database
    DATABASE_URL: str = "sqlite:///./holduntil.db"

    # Meta Business API Settings
    META_API_VERSION: str = "v18.0"
    META_VERIFY_TOKEN: str = "holduntil_verify_token_123"
    META_ACCESS_TOKEN: str = "mock_meta_access_token"
    META_PHONE_NUMBER_ID: str = "mock_phone_number_id"
    BOT_WHATSAPP_NUMBER: str = "254107560742"

    # Safaricom Daraja (M-Pesa) Settings
    DARAJA_ENV: str = "sandbox"  # sandbox or production
    DARAJA_CONSUMER_KEY: str = "mock_consumer_key"
    DARAJA_CONSUMER_SECRET: str = "mock_consumer_secret"
    DARAJA_SHORTCODE: str = "174379"  # Paybill/Buy Goods
    DARAJA_PASSKEY: str = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
    DARAJA_B2C_SHORTCODE: str = "600000"
    DARAJA_INITIATOR_NAME: str = "testapi"
    DARAJA_SECURITY_CREDENTIAL: str = "mock_security_credential"
    DARAJA_CALLBACK_URL: str = "https://example.com/api/webhook/daraja/callback"
    DARAJA_B2C_CALLBACK_URL: str = "https://example.com/api/webhook/daraja/b2c_callback"

    # AI Moderator Settings (Gemini)
    GEMINI_API_KEY: str = ""

    # Business Rules
    ESCROW_FEE_PERCENT: float = 1.5  # 1.5% fee
    DEFAULT_TRUST_SCORE: float = 100.0
    MIN_DEALS_FOR_BADGE: int = 10
    MIN_TRADES_FOR_PROFILE_STATS: int = 3
    ESCALATION_LIMIT_PER_USER_PER_MONTH: int = 2
    ESCALATION_FEE_KES: float = 200.0  # escalation fee (refundable on overturn)
    DELIVERY_GRACE_PERIOD_HOURS: int = 48  # time buyer has to confirm delivery
    APPEAL_WINDOW_HOURS: int = 24  # hours buyer/seller has to file an appeal
    DISPUTE_RESPONSE_WINDOW_HOURS: int = 24  # hours non-filer has to respond to a dispute
    SLA_BREACH_HOURS: int = 72  # SLA deadline for moderator resolution
    SLA_WARN_HOURS: int = 48  # SLA warning threshold for moderator resolution

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "config_settings.json")

def load_config(settings_obj):
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(settings_obj, k):
                    # Coerce values appropriately
                    val_type = type(getattr(settings_obj, k))
                    setattr(settings_obj, k, val_type(v))
            print("Loaded dynamic configurations successfully.")
        except Exception as e:
            print(f"Error loading config settings: {e}")

def save_config(settings_obj):
    try:
        data = {
            "ESCROW_FEE_PERCENT": settings_obj.ESCROW_FEE_PERCENT,
            "MIN_DEALS_FOR_BADGE": settings_obj.MIN_DEALS_FOR_BADGE,
            "MIN_TRADES_FOR_PROFILE_STATS": settings_obj.MIN_TRADES_FOR_PROFILE_STATS,
            "ESCALATION_FEE_KES": settings_obj.ESCALATION_FEE_KES,
            "DELIVERY_GRACE_PERIOD_HOURS": settings_obj.DELIVERY_GRACE_PERIOD_HOURS,
            "APPEAL_WINDOW_HOURS": settings_obj.APPEAL_WINDOW_HOURS,
            "DISPUTE_RESPONSE_WINDOW_HOURS": settings_obj.DISPUTE_RESPONSE_WINDOW_HOURS,
            "SLA_BREACH_HOURS": settings_obj.SLA_BREACH_HOURS,
            "SLA_WARN_HOURS": settings_obj.SLA_WARN_HOURS,
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=4)
        print("Saved dynamic configurations successfully.")
    except Exception as e:
        print(f"Error saving config settings: {e}")

load_config(settings)


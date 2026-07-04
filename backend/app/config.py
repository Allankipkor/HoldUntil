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

    # Safaricom Daraja (M-Pesa) Settings
    DARAJA_ENV: str = "sandbox"  # sandbox or production
    DARAJA_CONSUMER_KEY: str = "mock_consumer_key"
    DARAJA_CONSUMER_SECRET: str = "mock_consumer_secret"
    DARAJA_SHORTCODE: str = "174379"  # Paybill/Buy Goods
    DARAJA_PASSKEY: str = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
    DARAJA_B2C_SHORTCODE: str = "600000"
    DARAJA_INITIATOR_NAME: str = "testapi"
    DARAJA_SECURITY_CREDENTIAL: str = "mock_security_credential"
    DARAJA_CALLBACK_URL: str = "https://example.com/api/daraja/callback"
    DARAJA_B2C_CALLBACK_URL: str = "https://example.com/api/daraja/b2c_callback"

    # AI Moderator Settings (Gemini)
    GEMINI_API_KEY: str = ""

    # Business Rules
    ESCROW_FEE_PERCENT: float = 1.5  # 1.5% fee
    DEFAULT_TRUST_SCORE: float = 100.0
    MIN_DEALS_FOR_BADGE: int = 10
    ESCALATION_LIMIT_PER_USER_PER_MONTH: int = 2
    ESCALATION_FEE_KES: float = 200.0  # escalation fee (refundable on overturn)
    DELIVERY_GRACE_PERIOD_HOURS: int = 48  # time buyer has to confirm delivery

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

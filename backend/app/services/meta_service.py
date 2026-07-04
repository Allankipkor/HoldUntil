import requests
from datetime import datetime
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import User, UserRole, PlatformType, ChatLog
import logging

logger = logging.getLogger("meta_service")

# A fixed UUID to represent the system bot in chat logs
SYSTEM_BOT_ID = "00000000-0000-0000-0000-000000000000"

class MetaService:
    @staticmethod
    def _ensure_bot_user_exists(db: Session) -> str:
        """Ensure the system bot user is present in the database."""
        bot = db.query(User).filter(User.id == SYSTEM_BOT_ID).first()
        if not bot:
            bot = User(
                id=SYSTEM_BOT_ID,
                platform=PlatformType.WHATSAPP,
                phone_or_handle="HoldUntil_Bot",
                role=UserRole.ADMIN,
                trust_score=100.0
            )
            db.add(bot)
            db.commit()
        return SYSTEM_BOT_ID

    @classmethod
    def send_text_message(cls, db: Session, platform: str, recipient: str, text: str, deal_id: str = None) -> bool:
        """
        Send a text message via Meta Graph API or simulate it locally.
        Logs all outgoing messages in the database.
        """
        logger.info(f"Sending message to {recipient} via {platform}: {text}")
        bot_id = cls._ensure_bot_user_exists(db)

        # Log the outgoing message in chat_logs if a deal is specified
        if deal_id:
            chat_log = ChatLog(
                deal_id=deal_id,
                sender_id=bot_id,
                message_content=text,
                timestamp=datetime.utcnow()
            )
            db.add(chat_log)
            db.commit()

        if settings.SIMULATION_MODE:
            logger.info(f"[SIMULATION] Outgoing message logged for deal {deal_id}")
            return True

        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        # WhatsApp Graph API payload format
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {
                "body": text
            }
        }

        # Handle other platforms if needed
        if platform in ["messenger", "instagram"]:
            url = f"https://graph.facebook.com/{settings.META_API_VERSION}/me/messages"
            payload = {
                "recipient": {"id": recipient},
                "message": {"text": text}
            }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Meta message: {e}")
            return False

    @classmethod
    def send_template_message(cls, db: Session, platform: str, recipient: str, template_name: str, language: str = "en", components: list = None, deal_id: str = None) -> bool:
        """
        Send a pre-approved Meta utility template message when outside the 24h window.
        """
        bot_id = cls._ensure_bot_user_exists(db)
        
        # Format a descriptive text representation for simulation and logging
        component_text = f" [Template: {template_name}]"
        if components:
            component_text += f" with parameters: {components}"
        
        logger.info(f"Sending template {template_name} to {recipient} via {platform}")

        if deal_id:
            chat_log = ChatLog(
                deal_id=deal_id,
                sender_id=bot_id,
                message_content=f"[Utility Reminder] {template_name}:{component_text}",
                timestamp=datetime.utcnow()
            )
            db.add(chat_log)
            db.commit()

        if settings.SIMULATION_MODE:
            return True

        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language
                }
            }
        }
        if components:
            payload["template"]["components"] = components

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Meta template message: {e}")
            return False

    @classmethod
    def send_media_message(cls, db: Session, platform: str, recipient: str, media_url: str, caption: str = None, deal_id: str = None) -> bool:
        """
        Send image or media to the user.
        """
        bot_id = cls._ensure_bot_user_exists(db)

        if deal_id:
            chat_log = ChatLog(
                deal_id=deal_id,
                sender_id=bot_id,
                message_content=caption or "Sent media.",
                media_url=media_url,
                timestamp=datetime.utcnow()
            )
            db.add(chat_log)
            db.commit()

        if settings.SIMULATION_MODE:
            return True

        url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "image",
            "image": {
                "link": media_url
            }
        }
        if caption:
            payload["image"]["caption"] = caption

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Meta media message: {e}")
            return False

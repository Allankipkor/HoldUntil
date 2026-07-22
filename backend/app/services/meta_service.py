import requests
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import User, UserRole, PlatformType, ChatLog
import logging

logger = logging.getLogger("meta_service")

# A fixed UUID to represent the system bot in chat logs
SYSTEM_BOT_ID = "00000000-0000-0000-0000-000000000000"

class MetaService:
    APPROVED_TEMPLATES = {
        "deal_auto_refunded": "APPROVED",
        "deal_auto_released": "APPROVED",
        "dispute_appeal_reminder": "APPROVED",
        "dispute_response_lapsed": "APPROVED",
        "feedback_rating_prompt": "APPROVED",
        "dispute_verdict_notification": "APPROVED",
        "dispute_filed_passive": "APPROVED",
        "deal_cancelled_passive": "APPROVED",
        "dispute_resolved_voluntary": "APPROVED",
        "deal_status_alert": "APPROVED"
    }

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
                timestamp=datetime.now(UTC).replace(tzinfo=None)
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
        # Validate template approval status
        status = cls.APPROVED_TEMPLATES.get(template_name, "PENDING")
        if status != "APPROVED":
            logger.error(f"Cannot send template '{template_name}': approval status is {status}")
            return cls._fallback_to_free_form(db, platform, recipient, template_name, components, deal_id)

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
                timestamp=datetime.now(UTC).replace(tzinfo=None)
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
            return cls._fallback_to_free_form(db, platform, recipient, template_name, components, deal_id)

    @classmethod
    def _fallback_to_free_form(cls, db: Session, platform: str, recipient: str, template_name: str, components: list = None, deal_id: str = None) -> bool:
        """
        Fall back to a free-form text message if the template call fails and 
        the recipient has messaged the bot within the last 24 hours.
        """
        # Find user record
        user = db.query(User).filter(User.phone_or_handle == recipient).first()
        if not user:
            logger.warning(f"Fallback failed: recipient user '{recipient}' not found in database.")
            return False

        # Find the last message sent by this user to the bot (where sender_id == user.id)
        last_incoming = db.query(ChatLog).filter(
            ChatLog.sender_id == user.id
        ).order_by(ChatLog.timestamp.desc()).first()

        if not last_incoming:
            logger.warning(f"Fallback skipped: no incoming message found for user '{recipient}'.")
            return False

        # Check if the last incoming message is within 24 hours
        time_diff = datetime.now(UTC).replace(tzinfo=None) - last_incoming.timestamp
        if time_diff.total_seconds() > 24 * 3600:
            logger.warning(f"Fallback skipped: user '{recipient}' last messaged {time_diff.total_seconds()/3600:.1f} hours ago (outside 24h window).")
            return False

        # Extract parameters from components
        params = []
        if components:
            for comp in components:
                if comp.get("type") == "body":
                    for param in comp.get("parameters", []):
                        params.append(param.get("text", ""))

        # Reconstruct message body based on template_name
        message_text = ""
        try:
            if template_name == "deal_auto_refunded":
                message_text = f"⏰ Deal '{params[0]}' has been auto-refunded. KES {params[1]} is being returned to the buyer because the delivery deadline passed without verification."
            elif template_name == "deal_auto_released":
                message_text = f"⏰ Deal '{params[0]}' has been auto-completed. KES {params[1]} has been released to the seller because the buyer's 48-hour confirmation window lapsed."
            elif template_name == "dispute_appeal_reminder":
                message_text = f"⚠️ Reminder: You have 12 hours remaining to appeal the mediator's verdict for deal '{params[0]}'. Reply APPEAL to proceed."
            elif template_name == "dispute_response_lapsed":
                message_text = f"⏰ Dispute response deadline lapsed for deal '{params[0]}'. The non-responding party ({params[1]}) has been auto-resolved as lost."
            elif template_name == "feedback_rating_prompt":
                message_text = f"⭐ Transaction Complete!\n\nPlease rate your experience with the {params[1]} for deal '{params[0]}'. Reply with a number 1 to 5."
            elif template_name == "dispute_verdict_notification":
                message_text = f"⚖️ Dispute Verdict Alert for '{params[0]}':\n\nDecision: {params[1]}\nRationale: {params[2]}\n\n{params[3]}"
            elif template_name == "dispute_filed_passive":
                message_text = f"⚠️ The other party has filed a dispute for '{params[0]}'.\n\nReason: {params[1]}\n\nPlease respond with your statement and evidence within {params[2]} hours."
            elif template_name == "deal_cancelled_passive":
                message_text = f"❌ Transaction Alert for '{params[0]}':\n\n{params[1]}"
            elif template_name == "dispute_resolved_voluntary":
                message_text = f"🤝 Dispute Resolved for '{params[0]}':\n\n{params[1]}"
            elif template_name == "deal_status_alert":
                message_text = f"ℹ️ Deal Status Update for *{params[0]}*:\nStatus: {params[1]}\nDetails: {params[2]}\nNext Step: {params[3]}"
            else:
                message_text = f"Notification alert for deal: {', '.join(params)}"
        except IndexError:
            logger.error(f"Failed to reconstruct template fallback text for {template_name} (insufficient parameters: {params})")
            return False

        logger.info(f"Fallback triggered: sending free-form message to '{recipient}' within 24h window: {message_text}")
        return cls.send_text_message(db, platform, recipient, message_text, deal_id)

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
                timestamp=datetime.now(UTC).replace(tzinfo=None)
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

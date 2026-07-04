from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.config import settings
from backend.app.services.chat_bot import ChatBotService
from backend.app.services.meta_service import MetaService
from backend.app.models import ChatLog, PlatformType
import logging

logger = logging.getLogger("meta_webhook")
router = APIRouter(prefix="/webhook/meta", tags=["Meta Webhook"])

@router.get("")
def verify_meta_webhook(
    mode: str = Query(None, alias="hub.mode"),
    verify_token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    """Webhook verification endpoint called by Meta to register the webhook."""
    if mode and verify_token:
        if mode == "subscribe" and verify_token == settings.META_VERIFY_TOKEN:
            logger.info("Meta webhook verified successfully.")
            return int(challenge)
        else:
            logger.warning("Meta webhook verification failed: Token mismatch.")
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    raise HTTPException(status_code=400, detail="Missing verification parameters")

@router.post("")
async def receive_meta_message(request: Request, db: Session = Depends(get_db)):
    """
    Webhook handler for incoming WhatsApp, Messenger, and Instagram messages.
    Supports message receipt and message revocation events.
    """
    payload = await request.json()
    logger.debug(f"Received webhook payload: {payload}")

    # Log/track platform source
    obj = payload.get("object", "")
    platform = PlatformType.WHATSAPP
    if obj == "page":
        platform = PlatformType.MESSENGER
    elif obj == "instagram":
        platform = PlatformType.INSTAGRAM

    entry_list = payload.get("entry", [])
    for entry in entry_list:
        # Check for message revocation/delete callbacks
        # Standard WhatsApp webhook message deletion contains a list of messages with "status" or status updates
        # Check WhatsApp status updates or messages list
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            
            # Check for standard messages
            messages = value.get("messages", [])
            for msg in messages:
                sender = msg.get("from")
                msg_id = msg.get("id")
                msg_type = msg.get("type")
                
                # Check for deletion/revocation payload
                # In WhatsApp, when a message is deleted, it might send a message object with type "system" or a specific status structure.
                # If a message is deleted, WhatsApp sends a payload where "messages" has a property showing it was deleted,
                # or a status update. Let's capture it.
                if msg.get("status") == "deleted" or msg.get("type") == "revoked":
                    logger.info(f"Captured message revocation signal: {msg_id}")
                    # Update DB log
                    chat_log = db.query(ChatLog).filter(ChatLog.id == msg_id).first()
                    if chat_log:
                        chat_log.is_revoked = True
                        chat_log.revoked_at = datetime.utcnow()
                        db.commit()
                    continue

                text_content = ""
                media_url = None
                
                if msg_type == "text":
                    text_content = msg.get("text", {}).get("body", "")
                elif msg_type == "image":
                    # WhatsApp media payloads contain a media ID. We extract media url details
                    text_content = msg.get("image", {}).get("caption", "[Image Attachment]")
                    media_id = msg.get("image", {}).get("id")
                    media_url = f"https://mock-meta-cdn.sokosafi/media/{media_id}"
                elif msg_type == "document":
                    text_content = msg.get("document", {}).get("filename", "[Document]")
                    media_url = f"https://mock-meta-cdn.sokosafi/media/{msg.get('document', {}).get('id')}"

                if text_content or media_url:
                    # Run dialogue state machine
                    bot_reply = ChatBotService.process_message(
                        db=db,
                        platform=platform,
                        phone_or_handle=sender,
                        text=text_content,
                        media_url=media_url
                    )
                    
                    # Dispatch bot reply back to user
                    MetaService.send_text_message(
                        db=db,
                        platform=platform,
                        recipient=sender,
                        text=bot_reply,
                        deal_id=USER_SESSIONS.get(sender, {}).get("deal_id")
                    )

            # Check for status updates (sent, delivered, read, deleted)
            statuses = value.get("statuses", [])
            for status in statuses:
                status_type = status.get("status")
                recipient = status.get("recipient_id")
                msg_id = status.get("id")
                if status_type == "deleted" or status_type == "revoked":
                    logger.info(f"Captured status update revocation: {msg_id}")
                    chat_log = db.query(ChatLog).filter(ChatLog.id == msg_id).first()
                    if chat_log:
                        chat_log.is_revoked = True
                        chat_log.revoked_at = datetime.utcnow()
                        db.commit()

    return {"status": "success"}

# Reference back to sessions in bot service
from backend.app.services.chat_bot import USER_SESSIONS

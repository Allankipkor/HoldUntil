from datetime import datetime, UTC
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
                        chat_log.revoked_at = datetime.now(UTC).replace(tzinfo=None)
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
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    int_type = interactive.get("type")
                    if int_type == "nfm_reply":
                        nfm_reply = interactive.get("nfm_reply", {})
                        if nfm_reply.get("name") in ["flow", "profile_creation"]:
                            response_json = nfm_reply.get("response_json", "{}")
                            import json
                            try:
                                flow_data = json.loads(response_json)
                                bot_reply = ChatBotService.complete_onboarding(
                                    db=db,
                                    platform=platform,
                                    phone_or_handle=sender,
                                    data={
                                        "name": flow_data.get("name"),
                                        "payout_mpesa_number": flow_data.get("payout_mpesa_number"),
                                        "recovery_contact": flow_data.get("recovery_contact") or flow_data.get("recovery_email_or_phone"),
                                        "location": flow_data.get("location")
                                    }
                                )
                                from backend.app.services.chat_bot import USER_SESSIONS
                                deal_id = USER_SESSIONS.get(sender, {}).get("deal_id")
                                MetaService.send_text_message(
                                    db=db,
                                    platform=platform,
                                    recipient=sender,
                                    text=bot_reply,
                                    deal_id=deal_id,
                                    is_direct_reply=True
                                )
                            except Exception as parse_err:
                                logger.error(f"Error parsing webhook flow response json: {parse_err}")
                            continue

                if text_content or media_url:
                    # Capture active deal ID before running state machine resets it
                    from backend.app.services.chat_bot import USER_SESSIONS
                    from backend.app.models import User, Deal, DealStatus
                    
                    session_before = USER_SESSIONS.get(sender, {})
                    deal_id = session_before.get("deal_id")
                    if not deal_id:
                        user_db = db.query(User).filter(User.phone_or_handle == sender).first()
                        if user_db:
                            latest_deal = db.query(Deal).filter(
                                (Deal.seller_id == user_db.id) | (Deal.buyer_id == user_db.id)
                            ).order_by(Deal.created_at.desc()).first()
                            if latest_deal and latest_deal.status not in [DealStatus.COMPLETED, DealStatus.REFUNDED, DealStatus.CANCELLED]:
                                deal_id = latest_deal.id

                    # Run dialogue state machine
                    bot_reply = ChatBotService.process_message(
                        db=db,
                        platform=platform,
                        phone_or_handle=sender,
                        text=text_content,
                        media_url=media_url
                    )
                    
                    # If still not found, check if process_message just created/linked a new deal
                    if not deal_id:
                        deal_id = USER_SESSIONS.get(sender, {}).get("deal_id")
                    
                    # Dispatch bot reply back to user using the computed deal_id
                    MetaService.send_text_message(
                        db=db,
                        platform=platform,
                        recipient=sender,
                        text=bot_reply,
                        deal_id=deal_id,
                        is_direct_reply=True
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
                        chat_log.revoked_at = datetime.now(UTC).replace(tzinfo=None)
                        db.commit()

    return {"status": "success"}

# Reference back to sessions in bot service
from backend.app.services.chat_bot import USER_SESSIONS

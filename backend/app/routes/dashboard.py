import os
import io
import uuid
from datetime import datetime, UTC
from fastapi import APIRouter, Depends, HTTPException, Response, Query, UploadFile, File, Form
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.config import settings
from backend.app.models import Deal, DealStatus, User, UserRole, Dispute, DisputeTier, OutcomeType, Evidence, ChatLog, Payment, PaymentStatus, PlatformType
from backend.app.services.daraja_service import DarajaService
from backend.app.services.meta_service import MetaService, SYSTEM_BOT_ID
from backend.app.services.image_service import ImageService
from backend.app.services.ai_service import AIService
from PIL import Image, ImageDraw, ImageFont
import logging

logger = logging.getLogger("dashboard_routes")
router = APIRouter(prefix="/dashboard", tags=["Dashboard / Admin APIs"])

# ----------------------------------------------------
# ADMIN & SYSTEM ENDPOINTS
# ----------------------------------------------------

@router.get("/deals")
def get_all_deals(db: Session = Depends(get_db)):
    """Retrieve all deals in the system."""
    deals = db.query(Deal).order_by(Deal.created_at.desc()).all()
    result = []
    for d in deals:
        seller = db.query(User).filter(User.id == d.seller_id).first()
        buyer = db.query(User).filter(User.id == d.buyer_id).first() if d.buyer_id else None
        
        result.append({
            "id": d.id,
            "seller_handle": seller.phone_or_handle if seller else "Unknown",
            "buyer_handle": buyer.phone_or_handle if buyer else "Awaiting Buyer...",
            "item_description": d.item_description,
            "agreed_price": d.agreed_price,
            "delivery_deadline": d.delivery_deadline,
            "status": d.status,
            "created_at": d.created_at
        })
    return result

@router.get("/deals/{deal_id}")
def get_deal_detail(deal_id: str, db: Session = Depends(get_db)):
    """Retrieve detailed information about a single deal."""
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    seller = db.query(User).filter(User.id == deal.seller_id).first()
    buyer = db.query(User).filter(User.id == deal.buyer_id).first() if deal.buyer_id else None

    # Payments
    payments = db.query(Payment).filter(Payment.deal_id == deal.id).all()
    # Evidence
    evidences = db.query(Evidence).filter(Evidence.deal_id == deal.id).all()
    # ChatLogs
    chat_logs = db.query(ChatLog).filter(ChatLog.deal_id == deal.id).order_by(ChatLog.timestamp.asc()).all()
    # Disputes
    disputes = db.query(Dispute).filter(Dispute.deal_id == deal.id).all()

    return {
        "deal": {
            "id": deal.id,
            "seller_id": deal.seller_id,
            "seller_handle": seller.phone_or_handle if seller else "Unknown",
            "buyer_id": deal.buyer_id,
            "buyer_handle": buyer.phone_or_handle if buyer else None,
            "item_description": deal.item_description,
            "agreed_price": deal.agreed_price,
            "delivery_deadline": deal.delivery_deadline,
            "status": deal.status,
            "verification_code": deal.verification_code,
            "courier_name": deal.courier_name,
            "tracking_number": deal.tracking_number,
            "seller_confirmed": deal.seller_confirmed,
            "buyer_confirmed": deal.buyer_confirmed,
            "created_at": deal.created_at
        },
        "payments": [{
            "id": p.id,
            "amount": p.amount,
            "status": p.status,
            "stk_push_ref": p.stk_push_ref,
            "c2b_confirmation_ref": p.c2b_confirmation_ref,
            "b2c_payout_ref": p.b2c_payout_ref,
            "created_at": p.created_at
        } for p in payments],
        "evidences": [{
            "id": ev.id,
            "submitted_by_handle": "Seller" if ev.submitted_by == deal.seller_id else "Buyer",
            "file_url": ev.file_url,
            "perceptual_hash": ev.perceptual_hash,
            "exif_data": ev.exif_data,
            "dynamic_code_detected": ev.dynamic_code_detected,
            "courier_verified": ev.courier_verified,
            "created_at": ev.created_at
        } for ev in evidences],
        "chat_logs": [{
            "id": log.id,
            "sender_handle": "Seller" if log.sender_id == deal.seller_id else ("Buyer" if log.sender_id == (deal.buyer_id or "") else ("Bot" if log.sender_id == SYSTEM_BOT_ID else "Unknown")),
            "message_content": log.message_content,
            "media_url": log.media_url,
            "is_revoked": log.is_revoked,
            "revoked_at": log.revoked_at,
            "timestamp": log.timestamp
        } for log in chat_logs],
        "disputes": [{
            "id": disp.id,
            "filed_by_handle": "Seller" if disp.filed_by == deal.seller_id else "Buyer",
            "reason": disp.reason,
            "tier": disp.tier,
            "ai_decision": disp.ai_decision,
            "ai_reasoning": disp.ai_reasoning,
            "ai_confidence": disp.ai_confidence,
            "human_moderator_id": disp.human_moderator_id,
            "escalation_requested_by": disp.escalation_requested_by,
            "escalation_fee_paid": disp.escalation_fee_paid,
            "final_outcome": disp.final_outcome,
            "resolved_at": disp.resolved_at,
            "created_at": disp.created_at
        } for disp in disputes]
    }

@router.get("/disputes")
def get_disputes_queue(db: Session = Depends(get_db)):
    """Get the queue of active disputes, focusing on Tier 3 escalations."""
    disputes = db.query(Dispute).order_by(Dispute.created_at.desc()).all()
    result = []
    for d in disputes:
        deal = db.query(Deal).filter(Deal.id == d.deal_id).first()
        filer = db.query(User).filter(User.id == d.filed_by).first()
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        buyer = db.query(User).filter(User.id == deal.buyer_id).first()
        
        result.append({
            "id": d.id,
            "deal_id": d.deal_id,
            "item_description": deal.item_description if deal else "Unknown",
            "agreed_price": deal.agreed_price if deal else 0,
            "filed_by_handle": filer.phone_or_handle if filer else "Unknown",
            "seller_handle": seller.phone_or_handle if seller else "Unknown",
            "buyer_handle": buyer.phone_or_handle if buyer else "Unknown",
            "reason": d.reason,
            "tier": d.tier,
            "ai_decision": d.ai_decision,
            "ai_reasoning": d.ai_reasoning,
            "ai_confidence": d.ai_confidence,
            "resolved_at": d.resolved_at,
            "created_at": d.created_at
        })
    return result

@router.post("/disputes/{dispute_id}/resolve")
def resolve_dispute_manually(
    dispute_id: str,
    outcome: str = Form(...), # release, refund, partial_split
    partial_split_percentage: int = Form(None), # percentage to seller
    reasoning: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Tier 3: Human Moderator resolves a dispute, triggering payouts/refunds automatically.
    """
    dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
        
    deal = db.query(Deal).filter(Deal.id == dispute.deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
        
    seller = db.query(User).filter(User.id == deal.seller_id).first()
    buyer = db.query(User).filter(User.id == deal.buyer_id).first()

    # Track AI overturn metric
    if dispute.ai_decision and dispute.ai_decision != outcome:
        # User/moderator overturned the AI's decision!
        # Increment the AI flag counts for tracing
        seller.ai_overturn_flag_count += 1
        logger.info(f"AI Decision ({dispute.ai_decision}) was overturned by human to ({outcome}) for deal {deal.id}")

    dispute.final_outcome = OutcomeType(outcome)
    dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    dispute.tier = DisputeTier.TIER_3_HUMAN

    # Trigger payouts
    if outcome == "release":
        deal.status = DealStatus.COMPLETED
        # release to seller
        DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, deal.agreed_price, is_refund=False)
        MetaService.send_text_message(db, PlatformType.WHATSAPP, seller.phone_or_handle, f"🎉 Dispute resolved! Human moderator released KES {deal.agreed_price:.2f} to your account. Rationale: {reasoning}", deal.id)
        MetaService.send_text_message(db, PlatformType.WHATSAPP, buyer.phone_or_handle, f"ℹ️ Dispute resolved: Human moderator released funds to the seller. Rationale: {reasoning}", deal.id)
    elif outcome == "refund":
        deal.status = DealStatus.REFUNDED
        # refund to buyer
        DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, deal.agreed_price, is_refund=True)
        MetaService.send_text_message(db, PlatformType.WHATSAPP, buyer.phone_or_handle, f"🎉 Dispute resolved! Human moderator refunded KES {deal.agreed_price:.2f} to your account. Rationale: {reasoning}", deal.id)
        MetaService.send_text_message(db, PlatformType.WHATSAPP, seller.phone_or_handle, f"ℹ️ Dispute resolved: Human moderator refunded funds to the buyer. Rationale: {reasoning}", deal.id)
    elif outcome == "partial_split":
        deal.status = DealStatus.COMPLETED
        pct = partial_split_percentage or 50
        seller_amt = (pct / 100.0) * deal.agreed_price
        buyer_amt = deal.agreed_price - seller_amt
        
        DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, seller_amt, is_refund=False)
        DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, buyer_amt, is_refund=True)
        
        MetaService.send_text_message(db, PlatformType.WHATSAPP, seller.phone_or_handle, f"⚖️ Dispute resolved via Partial Split ({pct}% to seller). Received KES {seller_amt:.2f}. Rationale: {reasoning}", deal.id)
        MetaService.send_text_message(db, PlatformType.WHATSAPP, buyer.phone_or_handle, f"⚖️ Dispute resolved via Partial Split ({100-pct}% to buyer). Received KES {buyer_amt:.2f}. Rationale: {reasoning}", deal.id)

    db.commit()
    return {"status": "resolved", "final_outcome": outcome}

@router.get("/users")
def get_users_list(db: Session = Depends(get_db)):
    return db.query(User).all()

@router.post("/users/{user_id}/override")
def override_user_trust(user_id: str, trust_score: float = Form(None), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if trust_score is not None:
        user.trust_score = trust_score
    db.commit()
    return {"status": "updated", "user_id": user_id, "trust_score": user.trust_score}

# ----------------------------------------------------
# PUBLIC VERIFICATION & BADGE ENDPOINTS
# ----------------------------------------------------

@router.get("/verify/{seller_id}")
def verify_seller_public(seller_id: str, db: Session = Depends(get_db)):
    """Publicly viewable verification page details."""
    seller = db.query(User).filter(User.id == seller_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    # Calculate metrics
    completed_deals = db.query(Deal).filter(
        Deal.seller_id == seller.id,
        Deal.status == DealStatus.COMPLETED
    ).count()

    total_disputes = db.query(Dispute).join(Deal).filter(
        Deal.seller_id == seller.id
    ).count()

    # Dispute rate
    total_deals = db.query(Deal).filter(Deal.seller_id == seller.id).count()
    dispute_rate = (total_disputes / total_deals * 100.0) if total_deals > 0 else 0.0

    # Badge availability criteria
    has_badge = (completed_deals >= settings.MIN_DEALS_FOR_BADGE) and (seller.trust_score >= 85.0)

    return {
        "id": seller.id,
        "phone_or_handle": seller.phone_or_handle,
        "trust_score": seller.trust_score,
        "completed_deals": completed_deals,
        "dispute_rate_pct": round(dispute_rate, 1),
        "ai_overturn_count": seller.ai_overturn_flag_count,
        "has_badge": has_badge,
        "member_since": seller.created_at
    }

@router.get("/verify/{seller_id}/badge.png")
def generate_seller_badge(seller_id: str, db: Session = Depends(get_db)):
    """
    Generate and draw a Verified Safe Seller badge image dynamically using Pillow.
    No placeholders used, premium design.
    """
    seller = db.query(User).filter(User.id == seller_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    completed_deals = db.query(Deal).filter(
        Deal.seller_id == seller.id,
        Deal.status == DealStatus.COMPLETED
    ).count()

    # Determine badge tier color based on trust
    trust = seller.trust_score
    badge_title = "VERIFIED SAFE SELLER"
    
    # Gradient backgrounds definitions
    if trust >= 95.0:
        # Emerald gradient for top tier
        color_start = (16, 185, 129)  # #10B981
        color_end = (4, 120, 87)       # #047857
    elif trust >= 85.0:
        # Cyan/Blue gradient
        color_start = (6, 182, 212)   # #06B6D4
        color_end = (3, 105, 120)      # #036978
    else:
        # Grayed out - unverified
        color_start = (156, 163, 175) # #9CA3AF
        color_end = (75, 85, 99)       # #4B5563
        badge_title = "STANDARD SELLER"

    # Create image canvas
    width, height = 450, 160
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw rounded gradient card
    # Draw gradient pixels
    for x in range(20, width - 20):
        # Blend ratio
        r = (x - 20) / (width - 40)
        curr_color = (
            int(color_start[0] + (color_end[0] - color_start[0]) * r),
            int(color_start[1] + (color_end[1] - color_start[1]) * r),
            int(color_start[2] + (color_end[2] - color_start[2]) * r),
            255
        )
        draw.line([(x, 15), (x, height - 15)], fill=curr_color)

    # Optional: Draw simple border or outline
    draw.rounded_rectangle([15, 10, width - 15, height - 10], radius=15, outline=(255, 255, 255, 100), width=3)

    # Use default font for safety (avoid file loading crashes)
    font = ImageFont.load_default()

    # Draw Text
    draw.text((35, 25), "HoldUntil Escrow", fill=(255, 255, 255, 200), font=font)
    draw.text((35, 45), badge_title, fill=(255, 255, 255, 255), font=font)
    
    # Detail rows
    draw.text((35, 75), f"Trust Score: {trust:.1f}%", fill=(255, 255, 255, 255), font=font)
    draw.text((35, 95), f"Completed Deals: {completed_deals}", fill=(255, 255, 255, 230), font=font)
    draw.text((35, 115), f"ID: {seller.id[:12]}...", fill=(255, 255, 255, 150), font=font)

    # Add a visual checkmark shape or badge icon
    # Draw simple yellow star/checkmark
    draw.polygon([(width - 70, 75), (width - 55, 95), (width - 35, 55), (width - 55, 85)], fill=(253, 224, 71, 255)) # yellow check

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    return Response(content=buffer.getvalue(), media_type="image/png")

# ----------------------------------------------------
# SIMULATION SANDBOX OVERRIDES
# ----------------------------------------------------

@router.post("/simulation/mock-mpesa-payment")
def simulate_mpesa_payment(checkout_id: str = Form(...), db: Session = Depends(get_db)):
    """
    Force simulate a Daraja C2B/STK Push success webhook call for a transaction.
    """
    payment = db.query(Payment).filter(Payment.stk_push_ref == checkout_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Checkout ID not found")

    deal = db.query(Deal).filter(Deal.id == payment.deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Linked deal not found")

    # Build simulated webhook callback payload
    payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": f"mock_m_{uuid.uuid4().hex[:6]}",
                "CheckoutRequestID": checkout_id,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": deal.agreed_price},
                        {"Name": "MpesaReceiptNumber", "Value": f"M_REC_{uuid.uuid4().hex[:8].upper()}"},
                        {"Name": "TransactionDate", "Value": 20260704173000},
                        {"Name": "PhoneNumber", "Value": 254712345678}
                    ]
                }
            }
        }
    }

    # Route request locally
    import requests
    # Rather than making a network request, we can directly update it using our callback code
    # Or call webhook endpoint logic
    from backend.app.routes.daraja_webhook import mpesa_stk_callback
    # For safety, let's just trigger the database updates directly:
    receipt_no = f"MPESA_{uuid.uuid4().hex[:8].upper()}"
    payment.status = PaymentStatus.PAID
    payment.c2b_confirmation_ref = receipt_no
    deal.status = DealStatus.FUNDED
    deal.verification_code = f"HU-{uuid.uuid4().hex[:4].upper()}"
    db.commit()

    # Trigger dialogue notification bots
    MetaService.send_text_message(
        db, PlatformType.WHATSAPP, deal.buyer.phone_or_handle,
        f"💰 M-Pesa Payment of KES {deal.agreed_price:.2f} received successfully! Receipt: {receipt_no}.\n\n"
        f"The funds are safely locked in HoldUntil escrow. The seller has been notified to ship the item.",
        deal.id
    )

    MetaService.send_text_message(
        db, PlatformType.WHATSAPP, deal.seller.phone_or_handle,
        f"🎉 Payment Confirmed!\n\n"
        f"Buyer has paid KES {deal.agreed_price:.2f} into secure escrow.\n"
        f"Please ship the item as agreed.\n\n"
        f"Your unique delivery code is: **{deal.verification_code}**\n"
        f"When delivering, write this code on the package and take a photo. "
        f"Upload the photo here as proof of delivery.\n\n"
        f"Or reply: 'SHIPPED <courier_tracking_number>'",
        deal.id
    )

    return {"status": "payment_simulated", "receipt": receipt_no, "deal_status": deal.status}

@router.post("/simulation/upload-evidence")
def upload_simulated_evidence(
    deal_id: str = Form(...),
    sender_id: str = Form(...),
    photo_name: str = Form(...), # e.g. "package_with_code.jpg" or "FAIL_CODE" to test code failure
    db: Session = Depends(get_db)
):
    """
    Simulate uploading photo evidence during a transaction.
    Computes hashes and EXIF data.
    """
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    user = db.query(User).filter(User.id == sender_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Generate mock perceptual hash and EXIF
    # In simulation, we make a random unique hash but tag it if duplicate testing is needed
    phash = f"hash_{uuid.uuid4().hex[:12]}"
    if "REUSE" in photo_name.upper():
        # Match an existing evidence hash if one exists, otherwise set a specific duplicate hash
        existing = db.query(Evidence).first()
        phash = existing.perceptual_hash if existing else "hash_duplicate_reused_12345"

    exif = {
        "Make": "Apple",
        "Model": "iPhone 13 Pro",
        "DateTime": datetime.now(UTC).strftime("%Y:%m-%d %H:%M:%S"),
        "Software": "iOS 16.2",
        "GPSInfo": {"Latitude": -1.2921, "Longitude": 36.8219} # Nairobi GPS
    }

    # Custom triggers
    if "EXIF_FAIL" in photo_name.upper():
        exif = {} # missing EXIF
    if "EXIF_FUTURE" in photo_name.upper():
        exif["DateTime"] = "2028-10-10 12:00:00" # wrong timeline

    # Code matching
    code_verified = False
    if deal.verification_code:
        # If photo name indicates dynamic code mismatch, fail code verification
        code_verified = ("FAIL_CODE" not in photo_name.upper())

    # Add evidence
    evidence = Evidence(
        deal_id=deal.id,
        submitted_by=user.id,
        file_url=f"/simulated/media/{photo_name}",
        perceptual_hash=phash,
        exif_data=exif,
        dynamic_code_detected=code_verified,
        courier_verified=False
    )
    db.add(evidence)
    
    # Update deal status
    deal.status = DealStatus.SHIPPED
    db.commit()

    # Log chat statement that evidence was uploaded
    chat_log = ChatLog(
        deal_id=deal.id,
        sender_id=user.id,
        message_content=f"[Media Attachment Uploaded: {photo_name}]",
        media_url=f"/simulated/media/{photo_name}",
        timestamp=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(chat_log)
    db.commit()

    # Prompt buyer
    MetaService.send_text_message(
        db, PlatformType.WHATSAPP, deal.buyer.phone_or_handle,
        f"📦 The seller has uploaded photo proof of delivery: {photo_name}.\n\n"
        f"Did you receive your item as described? Reply YES or NO",
        deal.id
    )

    return {"status": "evidence_uploaded", "evidence_id": evidence.id, "code_verified": code_verified}

@router.post("/simulation/revoke-message")
def simulate_message_revocation(chat_log_id: str = Form(...), db: Session = Depends(get_db)):
    """Simulate a user deleting/recalling a message on WhatsApp."""
    chat_log = db.query(ChatLog).filter(ChatLog.id == chat_log_id).first()
    if not chat_log:
        raise HTTPException(status_code=404, detail="Chat log not found")

    chat_log.is_revoked = True
    chat_log.revoked_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    
    logger.info(f"Simulated revocation for chat log {chat_log_id}")
    return {"status": "message_revoked", "chat_log_id": chat_log_id, "revoked_at": chat_log.revoked_at}

@router.post("/simulation/reset-all")
def reset_sandbox_database(db: Session = Depends(get_db)):
    """Reset the sandbox database by deleting all transactions, disputes, and sessions."""
    # Wipe database tables
    db.query(Payment).delete()
    db.query(Dispute).delete()
    db.query(Evidence).delete()
    db.query(ChatLog).delete()
    db.query(Deal).delete()
    db.query(User).filter(User.id != SYSTEM_BOT_ID).delete()
    db.commit()
    
    # Wipe in-memory user sessions
    from backend.app.services.chat_bot import USER_SESSIONS
    USER_SESSIONS.clear()
    
    return {"status": "success", "message": "Database and active sessions reset successfully."}

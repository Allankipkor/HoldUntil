import uuid
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.database import get_db
from backend.app.models import Payment, PaymentStatus, Deal, DealStatus, PlatformType, Dispute
from backend.app.services.meta_service import MetaService
import logging

logger = logging.getLogger("daraja_webhook")
router = APIRouter(prefix="/webhook/daraja", tags=["Daraja Webhook"])

@router.post("/callback")
async def mpesa_stk_callback(request: Request, db: Session = Depends(get_db)):
    """Callback endpoint for Safaricom STK Push results."""
    try:
        payload = await request.json()
        logger.info(f"Received M-Pesa STK Callback: {payload}")

        body = payload.get("Body", {})
        stk_callback = body.get("stkCallback", {})
        checkout_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc")

        payment = db.query(Payment).filter(Payment.stk_push_ref == checkout_id).first()
        if not payment:
            logger.warning(f"Payment with CheckoutRequestID {checkout_id} not found.")
            return {"status": "ignored"}

        deal = db.query(Deal).filter(Deal.id == payment.deal_id).first()
        if not deal:
            logger.warning(f"Deal linked to payment {payment.id} not found.")
            return {"status": "ignored"}

        if result_code == 0:
            # Success!
            metadata_list = stk_callback.get("CallbackMetadata", {}).get("Item", [])
            receipt_no = None
            for item in metadata_list:
                if item.get("Name") == "MpesaReceiptNumber":
                    receipt_no = item.get("Value")
                    break

            payment.status = PaymentStatus.PAID
            payment.c2b_confirmation_ref = receipt_no or f"M_REC_{uuid.uuid4().hex[:8].upper()}"
            deal.status = DealStatus.FUNDED
            
            # Generate the dynamic package verification code
            deal.verification_code = f"HU-{uuid.uuid4().hex[:4].upper()}"
            db.commit()

            # Notify Buyer
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, deal.buyer.phone_or_handle,
                f"💰 M-Pesa Payment of KES {deal.agreed_price:.2f} received successfully! Receipt: {payment.c2b_confirmation_ref}.\n\n"
                f"The funds are safely locked in HoldUntil escrow. The seller has been notified to ship the item.",
                deal.id
            )

            # Notify Seller
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, deal.seller.phone_or_handle,
                f"🎉 Payment Confirmed!\n\n"
                f"Buyer has paid KES {deal.agreed_price:.2f} into secure escrow.\n"
                f"Please ship the item as agreed.\n\n"
                f"🔒 **Important Verification Requirement:**\n"
                f"Your unique delivery code is: **{deal.verification_code}**\n"
                f"When delivering, write this code on the package and take a photo. "
                f"Upload the photo here as proof of delivery.\n\n"
                f"Or reply: 'SHIPPED <courier_tracking_number>'",
                deal.id
            )
            logger.info(f"Payment success processed for Deal {deal.id}. Escrow funded.")
        else:
            # Cancelled or failed
            payment.status = PaymentStatus.FAILED
            deal.status = DealStatus.AWAITING_CONFIRMATION  # reset so they can retry
            db.commit()

            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, deal.buyer.phone_or_handle,
                f"❌ M-Pesa Payment failed or was cancelled: {result_desc}. Reply 'CONFIRM' to try again.",
                deal.id
            )
            logger.info(f"Payment failed processed for Deal {deal.id}: {result_desc}")

        return {"ResultCode": 0, "ResultDesc": "Success"}
    except Exception as err:
        logger.exception(f"Unhandled error in Daraja M-Pesa STK Callback handler: {err}")
        raise HTTPException(status_code=500, detail=f"Internal handler error: {str(err)}")

@router.post("/c2b/validation")
async def mpesa_c2b_validation():
    """Validation response to Safaricom C2B (must accept transaction)."""
    return {"ResultCode": 0, "ResultDesc": "Accepted"}

@router.post("/c2b/confirmation")
async def mpesa_c2b_confirmation(request: Request, db: Session = Depends(get_db)):
    """Confirmation endpoint for generic C2B Paybill inputs."""
    payload = await request.json()
    logger.info(f"Received M-Pesa C2B Confirmation: {payload}")
    # Handle direct Paybill transactions linking via AccountReference
    return {"ResultCode": 0, "ResultDesc": "Success"}

@router.post("/b2c/callback")
async def mpesa_b2c_callback(request: Request, db: Session = Depends(get_db)):
    """Callback endpoint for B2C payout operations."""
    payload = await request.json()
    logger.info(f"Received M-Pesa B2C Callback: {payload}")
    
    result = payload.get("Result", {})
    conversation_id = result.get("ConversationID")
    result_code = result.get("ResultCode")
    result_desc = result.get("ResultDesc")
    
    # 1. Check if this ConversationID matches a Dispute's appeal_fee_payout_ref
    dispute = db.query(Dispute).filter(Dispute.appeal_fee_payout_ref == conversation_id).first()
    if dispute:
        if result_code == 0:
            dispute.appeal_fee_refund_status = "completed"
            dispute.appeal_fee_refunded = True
        else:
            dispute.appeal_fee_refund_status = "failed"
            dispute.appeal_fee_refunded = False
        db.commit()
        logger.info(f"Processed B2C Appeal Refund callback for Dispute {dispute.id}, status: {dispute.appeal_fee_refund_status}")
        return {"ResultCode": 0, "ResultDesc": "Success"}

    # 2. Otherwise check if it matches a standard Payment's b2c_payout_ref
    payment = db.query(Payment).filter(Payment.b2c_payout_ref == conversation_id).first()
    if payment:
        deal = db.query(Deal).filter(Deal.id == payment.deal_id).first()
        if not deal:
            logger.warning(f"Deal linked to payment {payment.id} not found.")
            return {"status": "ignored"}

        is_refund = (payment.status == PaymentStatus.REFUND_PROCESSING)
        
        if result_code == 0:
            payment.status = PaymentStatus.REFUND_COMPLETED if is_refund else PaymentStatus.PAYOUT_COMPLETED
            deal.status = DealStatus.REFUNDED if is_refund else DealStatus.COMPLETED
            db.commit()
            
            from backend.app.services.rating_service import RatingService
            RatingService.trigger_post_deal_rating(db, deal)
            
            # Send notifications
            recipient_phone = deal.buyer.phone_or_handle if is_refund else deal.seller.phone_or_handle
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, recipient_phone,
                f"💰 M-Pesa B2C Payout of KES {payment.amount:.2f} completed successfully! Receipt Ref: {conversation_id}.",
                deal.id
            )
            logger.info(f"Processed B2C Deal Payout success callback for Payment {payment.id}")
        else:
            payment.status = PaymentStatus.FAILED
            deal.status = DealStatus.DISPUTED
            db.commit()
            
            recipient_phone = deal.buyer.phone_or_handle if is_refund else deal.seller.phone_or_handle
            MetaService.send_text_message(
                db, PlatformType.WHATSAPP, recipient_phone,
                f"❌ M-Pesa B2C Payout of KES {payment.amount:.2f} failed: {result_desc}.",
                deal.id
            )
            logger.warning(f"B2C Deal Payout failed callback for Payment {payment.id}: {result_desc}")
            
        return {"ResultCode": 0, "ResultDesc": "Success"}
        
    return {"ResultCode": 0, "ResultDesc": "Success"}

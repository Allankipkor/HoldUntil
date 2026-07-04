import base64
import requests
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import Payment, PaymentStatus, Deal, DealStatus
import logging

logger = logging.getLogger("daraja_service")

class DarajaService:
    @staticmethod
    def _get_access_token() -> str:
        """Fetch OAuth Access Token from Safaricom Daraja API."""
        if settings.SIMULATION_MODE:
            return "mock_oauth_access_token"

        url = f"https://{'sandbox' if settings.DARAJA_ENV == 'sandbox' else 'api'}.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        try:
            auth_str = f"{settings.DARAJA_CONSUMER_KEY}:{settings.DARAJA_CONSUMER_SECRET}"
            encoded_auth = base64.b64encode(auth_str.encode()).decode()
            headers = {"Authorization": f"Basic {encoded_auth}"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()["access_token"]
        except Exception as e:
            logger.error(f"Failed to fetch Daraja Access Token: {e}")
            raise Exception("Daraja auth failure")

    @classmethod
    def initiate_stk_push(cls, db: Session, deal: Deal, phone_number: str) -> dict:
        """
        Trigger an STK Push to the buyer's phone number.
        In simulation mode, we immediately return a success response and mock payment creation.
        """
        amount = int(deal.agreed_price)
        # Format phone to 2547XXXXXXXX
        formatted_phone = phone_number.replace("+", "").strip()
        if formatted_phone.startswith("0"):
            formatted_phone = "254" + formatted_phone[1:]
        elif not formatted_phone.startswith("254"):
            formatted_phone = "254" + formatted_phone

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        
        if settings.SIMULATION_MODE:
            mock_checkout_id = f"ws_CO_{uuid.uuid4().hex[:16]}"
            payment = Payment(
                deal_id=deal.id,
                stk_push_ref=mock_checkout_id,
                amount=deal.agreed_price,
                status=PaymentStatus.PENDING
            )
            db.add(payment)
            db.commit()
            
            logger.info(f"[SIMULATION] Mock STK Push initiated for Deal {deal.id}, checkout ID: {mock_checkout_id}")
            return {
                "ResponseCode": "0",
                "ResponseDescription": "Success. Request accepted for processing",
                "MerchantRequestID": f"mock_merchant_{uuid.uuid4().hex[:8]}",
                "CheckoutRequestID": mock_checkout_id,
                "CustomerMessage": "Success. Request accepted for processing",
                "simulation": True
            }

        access_token = cls._get_access_token()
        password_str = f"{settings.DARAJA_SHORTCODE}{settings.DARAJA_PASSKEY}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()

        url = f"https://{'sandbox' if settings.DARAJA_ENV == 'sandbox' else 'api'}.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        
        payload = {
            "BusinessShortCode": settings.DARAJA_SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": formatted_phone,
            "PartyB": settings.DARAJA_SHORTCODE,
            "PhoneNumber": formatted_phone,
            "CallBackURL": settings.DARAJA_CALLBACK_URL,
            "AccountReference": f"Deal-{deal.id[:8]}",
            "TransactionDesc": f"HoldUntil Escrow Deal {deal.id[:8]}"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            response_json = response.json()
            if response.status_code == 200 and response_json.get("ResponseCode") == "0":
                checkout_req_id = response_json["CheckoutRequestID"]
                payment = Payment(
                    deal_id=deal.id,
                    stk_push_ref=checkout_req_id,
                    amount=deal.agreed_price,
                    status=PaymentStatus.PENDING
                )
                db.add(payment)
                db.commit()
                return response_json
            else:
                logger.error(f"Daraja STK push failure response: {response_json}")
                raise Exception(response_json.get("ResponseDescription", "Daraja push rejected"))
        except Exception as e:
            logger.error(f"Error initiating STK push: {e}")
            raise Exception("Daraja connection error")

    @classmethod
    def initiate_b2c_payout(cls, db: Session, deal: Deal, phone_number: str, amount: float, is_refund: bool = False) -> dict:
        """
        Pay out the seller (or refund the buyer) via B2C.
        If SIMULATION_MODE, mock it immediately.
        """
        # Format phone to 2547XXXXXXXX
        formatted_phone = phone_number.replace("+", "").strip()
        if formatted_phone.startswith("0"):
            formatted_phone = "254" + formatted_phone[1:]
        elif not formatted_phone.startswith("254"):
            formatted_phone = "254" + formatted_phone

        payment = db.query(Payment).filter(Payment.deal_id == deal.id).first()
        if not payment:
            payment = Payment(deal_id=deal.id, amount=amount)
            db.add(payment)
        
        payment.status = PaymentStatus.PAYOUT_PROCESSING if not is_refund else PaymentStatus.REFUND_PROCESSING
        db.commit()

        if settings.SIMULATION_MODE:
            mock_ref = f"MPESA_B2C_{uuid.uuid4().hex[:10].upper()}"
            payment.b2c_payout_ref = mock_ref
            payment.status = PaymentStatus.PAYOUT_COMPLETED if not is_refund else PaymentStatus.REFUND_COMPLETED
            
            # Transition deal status
            deal.status = DealStatus.COMPLETED if not is_refund else DealStatus.REFUNDED
            db.commit()
            
            logger.info(f"[SIMULATION] Mock B2C payout completed for Deal {deal.id}, ref: {mock_ref}")
            return {
                "OriginatorConversationID": f"mock_orig_{uuid.uuid4().hex[:8]}",
                "ConversationID": f"mock_conv_{uuid.uuid4().hex[:8]}",
                "ResponseCode": "0",
                "ResponseDescription": "Accept the service request successfully.",
                "b2c_payout_ref": mock_ref,
                "simulation": True
            }

        access_token = cls._get_access_token()
        url = f"https://{'sandbox' if settings.DARAJA_ENV == 'sandbox' else 'api'}.safaricom.co.ke/mpesa/b2c/v1/paymentrequest"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        
        payload = {
            "InitiatorName": settings.DARAJA_INITIATOR_NAME,
            "SecurityCredential": settings.DARAJA_SECURITY_CREDENTIAL,
            "CommandID": "BusinessPayment" if not is_refund else "PromotionPayment",
            "Amount": int(amount),
            "PartyA": settings.DARAJA_B2C_SHORTCODE,
            "PartyB": formatted_phone,
            "Remarks": f"Payout for Deal {deal.id[:8]}",
            "QueueTimeOutURL": settings.DARAJA_B2C_CALLBACK_URL,
            "ResultURL": settings.DARAJA_B2C_CALLBACK_URL,
            "Occasion": "EscrowPayout" if not is_refund else "EscrowRefund"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            response_json = response.json()
            if response.status_code == 200 and response_json.get("ResponseCode") == "0":
                logger.info(f"B2C Payout request accepted by Daraja: {response_json}")
                return response_json
            else:
                payment.status = PaymentStatus.FAILED
                db.commit()
                logger.error(f"Daraja B2C payout failure response: {response_json}")
                raise Exception(response_json.get("ResponseDescription", "Daraja B2C rejected"))
        except Exception as e:
            payment.status = PaymentStatus.FAILED
            db.commit()
            logger.error(f"Error initiating B2C Payout: {e}")
            raise Exception("Daraja B2C connection error")

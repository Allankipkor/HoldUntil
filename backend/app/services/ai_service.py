import json
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import Dispute, Deal, User, ChatLog, Evidence, OutcomeType
from backend.app.services.couriers import verify_tracking
from backend.app.services.image_service import ImageService
import logging

logger = logging.getLogger("ai_service")

class AIService:
    @classmethod
    def run_moderation(cls, db: Session, dispute_id: str) -> dict:
        """
        Run Tier 2 AI Dispute Resolution.
        Pulls context, executes checks, sends to Gemini, and logs reasoning.
        """
        dispute = db.query(Dispute).filter(Dispute.id == dispute_id).first()
        if not dispute:
            raise Exception("Dispute not found")

        deal = db.query(Deal).filter(Deal.id == dispute.deal_id).first()
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        buyer = db.query(User).filter(User.id == deal.buyer_id).first()
        
        # 1. Fetch transcripts
        logs = db.query(ChatLog).filter(ChatLog.deal_id == deal.id).order_by(ChatLog.timestamp.asc()).all()
        transcript_lines = []
        for log in logs:
            sender_label = "Seller" if log.sender_id == seller.id else ("Buyer" if log.sender_id == buyer.id else "System Bot")
            status_label = " [DELETED BY USER]" if log.is_revoked else ""
            transcript_lines.append(f"[{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {sender_label}: {log.message_content}{status_label}")
        transcript = "\n".join(transcript_lines)

        # 2. Fetch Evidence & run checks
        evidences = db.query(Evidence).filter(Evidence.deal_id == deal.id).all()
        evidence_summary = []
        
        for i, ev in enumerate(evidences):
            submitter_label = "Seller" if ev.submitted_by == seller.id else "Buyer"
            
            # Check courier tracking if available
            courier_status = "Not Applicable"
            if deal.courier_name and deal.tracking_number:
                tracking_res = verify_tracking(deal.courier_name, deal.tracking_number)
                courier_status = tracking_res.get("status", "unknown")
                ev.courier_verified = (courier_status == "delivered")
                
            # Check photo reuse perceptual hashing
            reuse_matches = []
            if ev.perceptual_hash:
                reuse_matches = ImageService.check_photo_reuse(db, ev.perceptual_hash)
            
            reuse_flag = "No reuse flags"
            if reuse_matches:
                reuse_flag = f"WARNING: Reused photo detected! Matches found in {len(reuse_matches)} other deals."

            # Check dynamic code verification code
            code_verified = False
            if deal.verification_code:
                code_verified = ImageService.verify_dynamic_code(ev.file_url, deal.verification_code)
                ev.dynamic_code_detected = code_verified

            evidence_summary.append(
                f"Evidence #{i+1} by {submitter_label}:\n"
                f"- File URL: {ev.file_url}\n"
                f"- Dynamic Code '{deal.verification_code}' Present: {code_verified}\n"
                f"- Courier Verification: {courier_status}\n"
                f"- Photo Reuse check: {reuse_flag}\n"
                f"- EXIF: {json.dumps(ev.exif_data or {})}"
            )
            
        db.commit() # Save courier/code verification flags back to DB
        evidence_text = "\n\n".join(evidence_summary) if evidence_summary else "No file evidence submitted."

        # 3. Compile context payload
        context = {
            "deal_id": deal.id,
            "item_description": deal.item_description,
            "agreed_price": deal.agreed_price,
            "delivery_deadline": str(deal.delivery_deadline),
            "seller_trust_score": seller.trust_score,
            "buyer_trust_score": buyer.trust_score,
            "dispute_reason": dispute.reason,
            "transcript": transcript,
            "evidence": evidence_text
        }

        # 4. Prompt construction
        prompt = f"""
You are the HoldUntil AI Moderator, an automated escrow mediator resolving a dispute between a Buyer and a Seller in Kenya.
Your decision must be fair, neutral, and adhere strictly to the following rubric weights:
1. Delivery Evidence: Existence and verification of delivery (tracking status, photo presence).
2. Evidence Match: Does the item photo match the description?
3. Timeline Plausibility: Was the shipment dispatched within the agreed deadline?
4. Self-Contradiction: Check if any party contradictions exist in the chat logs.
5. Trust Score: User trust history is used as a tie-breaker ONLY.

DISPUTE CONTEXT:
- Item: {context['item_description']}
- Price: KES {context['agreed_price']}
- Delivery Deadline: {context['delivery_deadline']}
- Seller Trust Score: {context['seller_trust_score']}/100
- Buyer Trust Score: {context['buyer_trust_score']}/100
- Dispute Reason (filed by Buyer): {context['dispute_reason']}

EVIDENCE LOGS:
{context['evidence']}

CHAT TRANSCRIPT:
\"\"\"
{context['transcript']}
\"\"\"

DECISION INSTRUCTIONS:
- You must output your final verdict in valid JSON format.
- Output EXACTLY this JSON structure:
{{
  "outcome": "release" | "refund" | "partial_split",
  "partial_split_percentage": null | <integer 1 to 99>,
  "reasoning": "<clear explanation citing evidence and explaining who is at fault based on the rubric>",
  "confidence": <float 0.0 to 1.0>
}}
- "release" means releasing 100% of funds to the seller.
- "refund" means refunding 100% of funds to the buyer.
- "partial_split" means split funds. Specify the seller percentage in "partial_split_percentage" (e.g. 50).
- Do not fabricate facts. Absence of delivery evidence always counts against the seller. Photo reuse warnings are a critical indicator of fraud.
"""

        # 5. Execute LLM or Heuristics Fallback
        result = None
        if settings.GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel('gemini-1.5-flash') # use stable model
                response = model.generate_content(prompt)
                
                # Extract JSON block
                text_response = response.text
                json_match = re.search(r"\{.*\}", text_response, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    result = json.loads(text_response)
            except Exception as e:
                logger.error(f"Gemini API dispute resolution failed: {e}. Falling back to rule engine.")

        if not result:
            # Rule-based fallback engine
            result = cls._run_heuristics(deal, dispute, evidences)

        # 6. Save decision details
        dispute.ai_decision = OutcomeType(result["outcome"])
        dispute.ai_reasoning = result["reasoning"]
        dispute.ai_confidence = result["confidence"]
        
        # For Tier 2, if confidence is high (> 0.8), we can auto-apply the verdict!
        # Otherwise, or by default, we write the proposal and wait for dashboard review.
        # Let's auto-apply for AI moderator to keep it smooth, but write it down.
        # Wait, the prompt says "Tier 2 - AI Moderator (default for actual disputes): Required output: exactly one of Release / Refund / Partial split... human dashboard is Tier 3 (opt-in escalation only)".
        # Yes! So Tier 2 decisions auto-execute unless escalated! Let's execute the payout.
        if result["outcome"] == "release":
            dispute.final_outcome = OutcomeType.RELEASE
            dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            deal.status = DealStatus.COMPLETED
            try:
                DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, deal.agreed_price, is_refund=False)
            except Exception as pay_err:
                logger.error(f"Auto-payout error: {pay_err}")
        elif result["outcome"] == "refund":
            dispute.final_outcome = OutcomeType.REFUND
            dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            deal.status = DealStatus.REFUNDED
            try:
                DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, deal.agreed_price, is_refund=True)
            except Exception as refund_err:
                logger.error(f"Auto-refund error: {refund_err}")
        elif result["outcome"] == "partial_split":
            dispute.final_outcome = OutcomeType.PARTIAL_SPLIT
            dispute.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            deal.status = DealStatus.COMPLETED # split completed
            pct = result.get("partial_split_percentage", 50) or 50
            seller_amt = (pct / 100.0) * deal.agreed_price
            buyer_amt = deal.agreed_price - seller_amt
            try:
                DarajaService.initiate_b2c_payout(db, deal, seller.phone_or_handle, seller_amt, is_refund=False)
                DarajaService.initiate_b2c_payout(db, deal, buyer.phone_or_handle, buyer_amt, is_refund=True)
            except Exception as split_err:
                logger.error(f"Auto-split payout error: {split_err}")

        db.commit()
        return result

    @classmethod
    def _run_heuristics(cls, deal: Deal, dispute: Dispute, evidences: list) -> dict:
        """Heuristic rule-based dispute resolver fallback."""
        # Check if tracking is verified
        tracking_ok = False
        if deal.courier_name and deal.tracking_number:
            res = verify_tracking(deal.courier_name, deal.tracking_number)
            tracking_ok = (res.get("status") == "delivered")

        # Check evidence verification
        has_verified_photo = False
        has_reuse_alert = False
        
        for ev in evidences:
            if ev.dynamic_code_detected:
                has_verified_photo = True
            if ev.perceptual_hash and "WARNING" in str(ev.perceptual_hash):
                has_reuse_alert = True

        # Heuristic Logic
        if tracking_ok:
            return {
                "outcome": "release",
                "partial_split_percentage": None,
                "reasoning": "Courier tracking API confirms successful delivery to the buyer. This verified tracking data overrides buyer's claims of non-receipt.",
                "confidence": 0.95
            }
        
        if has_reuse_alert:
            return {
                "outcome": "refund",
                "partial_split_percentage": None,
                "reasoning": "System fraud detection flagged the seller's submitted package photo as recycled (previously submitted in another deal). Due to photo reuse fraud, the buyer is refunded.",
                "confidence": 0.90
            }

        if has_verified_photo:
            return {
                "outcome": "release",
                "partial_split_percentage": None,
                "reasoning": "Seller submitted physical photo evidence containing the correct dynamic deal verification code. Buyer claims are overruled.",
                "confidence": 0.85
            }

        # If no clear evidence on either side
        if not evidences and not tracking_ok:
            return {
                "outcome": "refund",
                "partial_split_percentage": None,
                "reasoning": "Seller failed to provide any delivery tracking number or photo evidence before the deadline. Under HoldUntil escrow rules, absence of proof results in automatic refund.",
                "confidence": 0.99
            }
            
        # Default fallback is split
        return {
            "outcome": "partial_split",
            "partial_split_percentage": 50,
            "reasoning": "Unable to verify physical delivery or claim accuracy from transcripts alone. Resolving via 50/50 split.",
            "confidence": 0.50
        }
import re

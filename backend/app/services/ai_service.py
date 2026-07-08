import json
import re
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import Dispute, Deal, User, ChatLog, Evidence, OutcomeType, DealStatus, PlatformType
from backend.app.services.couriers import verify_tracking
from backend.app.services.image_service import ImageService
from backend.app.services.meta_service import MetaService
from backend.app.services.daraja_service import DarajaService
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
            "evidence": evidence_text,
            "transaction_type": deal.transaction_type or "shipped",
            "courier_name": deal.courier_name or "N/A"
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
- Transaction Type: {context['transaction_type']}
- Courier Name: {context['courier_name']}
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
        
        # Determine filer and non-filer for notifications
        filer_user = seller if dispute.filed_by == seller.id else buyer
        non_filer_user = buyer if dispute.filed_by == seller.id else seller

        # AI acts as recommender only. The deal status remains DISPUTED.
        # Dispute resolved_at and final_outcome remain NULL.
        deal.status = DealStatus.DISPUTED
        
        # Send platform notifications indicating automated findings and routing to Human Moderator
        MetaService.send_text_message(
            db, PlatformType.WHATSAPP, filer_user.phone_or_handle,
            "🤖 HoldUntil Automated Check: We have analyzed the deal and logged the supporting evidence. Your dispute has been routed to a Human Moderator for a binding decision. Escrow funds will remain frozen until a decision is rendered.\n\n"
            "💡 You can resolve this informally: The buyer can reply 'RELEASE' to release funds to the seller, or the seller can reply 'REFUND' to return funds to the buyer.",
            deal.id
        )
        
        MetaService.send_text_message(
            db, PlatformType.WHATSAPP, non_filer_user.phone_or_handle,
            "🤖 HoldUntil Notification: A dispute has been filed. The case has been analyzed and routed to a human mediator. Escrow funds remain frozen.\n\n"
            "💡 You can resolve this informally: The buyer can reply 'RELEASE' to release funds, or the seller can reply 'REFUND' to return funds.",
            deal.id
        )

        db.commit()
        return result

    @classmethod
    def _run_heuristics(cls, deal: Deal, dispute: Dispute, evidences: list) -> dict:
        """Heuristic rule-based dispute resolver fallback."""
        # Fraud Check: check if any photo was reused (recycled photo)
        for ev in evidences:
            if ev.perceptual_hash and "WARNING" in str(ev.perceptual_hash):
                return {
                    "outcome": "refund",
                    "partial_split_percentage": None,
                    "reasoning": "System fraud detection flagged the seller's submitted proof photo as recycled (previously submitted in another deal). Buyer is refunded due to fraud.",
                    "confidence": 0.90
                }

        # Check by transaction type
        t_type = deal.transaction_type or "shipped"

        if t_type == "digital":
            # Type 1: Digital Deliverable
            # Seller must submit BOTH deliverable_file_url AND in-app photo proof
            has_file = any(ev.deliverable_file_url for ev in evidences)
            has_in_app_proof = any(ev.in_app_captured and ev.dynamic_code_detected for ev in evidences)
            
            if has_file and has_in_app_proof:
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "Seller submitted both the digital deliverable file and verified in-app screenshot proof of completion. Releasing funds to seller.",
                    "confidence": 0.95
                }
            elif has_in_app_proof:
                # No file, but has in-app proof (action-based service)
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "Seller submitted verified in-app screenshot proof of completion for the digital service. Releasing funds.",
                    "confidence": 0.85
                }
            else:
                return {
                    "outcome": "refund",
                    "partial_split_percentage": None,
                    "reasoning": "Seller failed to provide sufficient digital delivery or completion evidence. Refunding buyer.",
                    "confidence": 0.95
                }

        elif t_type == "shipped":
            # Type 2: Shipped Goods
            tracking_ok = False
            is_verified = False
            if deal.courier_name and deal.tracking_number:
                res = verify_tracking(deal.courier_name, deal.tracking_number)
                tracking_ok = (res.get("status") == "delivered")
                if deal.courier_name.lower() in ["sendy", "boxleo"]:
                    is_verified = True
                
            has_in_app_package = any(ev.in_app_captured and ev.dynamic_code_detected for ev in evidences)

            if tracking_ok and is_verified:
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "API-verified courier tracking confirms successful delivery to the buyer. This verified tracking data overrides buyer claims.",
                    "confidence": 0.98
                }
            elif tracking_ok:
                # Unverified API courier, or self-reported
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "Self-reported or unverified courier tracking shows delivered. Proceeding with release based on self-reported courier event.",
                    "confidence": 0.80
                }
            elif has_in_app_package:
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "Seller submitted in-app package photo with matching dynamic verification code, though API tracking is pending.",
                    "confidence": 0.85
                }
            else:
                return {
                    "outcome": "refund",
                    "partial_split_percentage": None,
                    "reasoning": "Seller failed to provide valid in-app package photo or verified tracking updates. Refunding buyer.",
                    "confidence": 0.99
                }

        elif t_type == "handoff":
            # Type 3: Local In-Person Handoff
            # Check for in-app-captured handoff photo
            has_handoff_photo = any(ev.in_app_captured and not ev.deliverable_file_url and not ev.video_call_log for ev in evidences)
            
            if has_handoff_photo:
                return {
                    "outcome": "release",
                    "partial_split_percentage": None,
                    "reasoning": "Seller submitted a verified in-app-captured handover photo matching the agreed description. Overruling buyer's receipt dispute.",
                    "confidence": 0.85
                }
            else:
                return {
                    "outcome": "refund",
                    "partial_split_percentage": None,
                    "reasoning": "Seller did not submit the mandatory in-app-captured handover photo as proof of local transaction completion. Refunding buyer.",
                    "confidence": 0.90
                }

        elif t_type == "remote_service":
            # Type 4: Remote Physical Service
            # Check for logged video call occurrence
            has_video_call = any(ev.video_call_log is not None for ev in evidences)
            
            if has_video_call:
                video_ev = next(ev for ev in evidences if ev.video_call_log is not None)
                log = video_ev.video_call_log
                buyer_pres = log.get("buyer_present", True) or log.get("proxy_name") is not None
                dur = log.get("duration_seconds", 0)
                
                if buyer_pres and dur > 5:
                    return {
                        "outcome": "release",
                        "partial_split_percentage": None,
                        "reasoning": f"Live in-app video call completion checkpoint took place (duration: {dur}s) with buyer/proxy attending. Verification confirmed.",
                        "confidence": 0.95
                    }
                else:
                    return {
                        "outcome": "refund",
                        "partial_split_percentage": None,
                        "reasoning": "A video call was logged, but buyer/proxy attendance was not verified or duration was too brief.",
                        "confidence": 0.80
                    }
            else:
                return {
                    "outcome": "refund",
                    "partial_split_percentage": None,
                    "reasoning": "Seller did not conduct the mandatory live in-app video call session to demonstrate completed work. Refunding buyer.",
                    "confidence": 0.90
                }

        # Fallback split
        return {
            "outcome": "partial_split",
            "partial_split_percentage": 50,
            "reasoning": "Unable to verify physical delivery or claim accuracy from transcripts alone. Resolving via 50/50 split.",
            "confidence": 0.50
        }

    @classmethod
    def apply_dispute_outcome(cls, db: Session, deal: Deal, dispute: Dispute, outcome: OutcomeType, partial_split_percentage: int = None):
        """
        Adjusts trust scores and recalculates win rates for buyer and seller.
        """
        seller = db.query(User).filter(User.id == deal.seller_id).first()
        buyer = db.query(User).filter(User.id == deal.buyer_id).first() if deal.buyer_id else None

        if outcome == OutcomeType.RELEASE:
            # Reward seller, penalize buyer (if buyer was the dispute filer)
            if seller:
                seller.trust_score = min(100.0, seller.trust_score + 2.0)
            if buyer and dispute.filed_by == buyer.id:
                buyer.trust_score = max(0.0, buyer.trust_score - 15.0)

        elif outcome == OutcomeType.REFUND:
            # Reward buyer, penalize seller (if seller committed fraud or failed delivery)
            if buyer:
                buyer.trust_score = min(100.0, buyer.trust_score + 2.0)
            if seller:
                seller.trust_score = max(0.0, seller.trust_score - 20.0)

        # Path 3 Automatic Dispute Outcome Ratings
        from backend.app.models import Rating, RatingSource, ResolutionMethod
        
        if dispute.resolution_method in [ResolutionMethod.HUMAN_FIRST_INSTANCE, ResolutionMethod.APPEAL]:
            # Delete any existing dispute_outcome ratings for this deal to avoid duplication
            db.query(Rating).filter(
                Rating.deal_id == deal.id,
                Rating.rating_source == RatingSource.DISPUTE_OUTCOME
            ).delete()
            
            if outcome == OutcomeType.RELEASE:
                seller_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.seller_id, score=1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                buyer_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.buyer_id, score=-1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                db.add_all([seller_rating, buyer_rating])
            elif outcome == OutcomeType.REFUND:
                seller_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.seller_id, score=-1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                buyer_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.buyer_id, score=1.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                db.add_all([seller_rating, buyer_rating])
            elif outcome == OutcomeType.PARTIAL_SPLIT:
                seller_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.seller_id, score=0.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                buyer_rating = Rating(deal_id=deal.id, rater_id=None, ratee_id=deal.buyer_id, score=0.0, rating_source=RatingSource.DISPUTE_OUTCOME, is_applied=True)
                db.add_all([seller_rating, buyer_rating])
            db.commit()

        db.commit()

        # Recalculate win rates for both parties
        if seller:
            cls.recalculate_user_metrics(db, seller)
        if buyer:
            cls.recalculate_user_metrics(db, buyer)
        db.commit()

    @classmethod
    def recalculate_user_metrics(cls, db: Session, user: User):
        """
        Recalculate dispute win rate based on resolved disputes historically.
        - A seller wins if final_outcome is RELEASE.
        - A buyer wins if final_outcome is REFUND.
        - PARTIAL_SPLIT counts as a 0.5 win for both.
        """
        resolved_disputes = db.query(Dispute).join(Deal).filter(
            Dispute.resolved_at != None,
            (Deal.seller_id == user.id) | (Deal.buyer_id == user.id)
        ).all()

        total = len(resolved_disputes)
        if total == 0:
            user.dispute_win_rate = 0.0
            return

        wins = 0.0
        for d in resolved_disputes:
            is_seller = (d.deal.seller_id == user.id)
            if d.final_outcome == OutcomeType.RELEASE:
                if is_seller:
                    wins += 1.0
            elif d.final_outcome == OutcomeType.REFUND:
                if not is_seller:
                    wins += 1.0
            elif d.final_outcome == OutcomeType.PARTIAL_SPLIT:
                wins += 0.5

        user.dispute_win_rate = float(wins) / total

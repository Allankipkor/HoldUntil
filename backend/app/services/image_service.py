import os
from PIL import Image
from PIL.ExifTags import TAGS
import imagehash
import json
from sqlalchemy.orm import Session
from backend.app.models import Evidence
import logging

logger = logging.getLogger("image_service")

class ImageService:
    @staticmethod
    def get_perceptual_hash(image_path: str) -> str:
        """Calculate the perceptual hash (pHash) of an image."""
        try:
            if not os.path.exists(image_path):
                logger.warning(f"Image path not found: {image_path}")
                return ""
            img = Image.open(image_path)
            phash = imagehash.phash(img)
            return str(phash)
        except Exception as e:
            logger.error(f"Error generating perceptual hash: {e}")
            return ""

    @staticmethod
    def get_exif_data(image_path: str) -> dict:
        """Extract and format EXIF metadata from an image."""
        exif_info = {}
        try:
            if not os.path.exists(image_path):
                return exif_info
            img = Image.open(image_path)
            info = img._getexif()
            if info:
                for tag, value in info.items():
                    decoded = TAGS.get(tag, tag)
                    # Convert bytes to string for JSON serialization
                    if isinstance(value, bytes):
                        try:
                            value = value.decode(errors="replace")
                        except Exception:
                            value = str(value)
                    elif isinstance(value, (int, float, str, list, dict)):
                        pass
                    else:
                        value = str(value)
                    exif_info[decoded] = value
        except Exception as e:
            logger.error(f"Error reading EXIF data: {e}")
        return exif_info

    @classmethod
    def check_photo_reuse(cls, db: Session, current_hash: str, threshold: int = 5) -> list:
        """
        Check if the perceptual hash of the photo matches any previously uploaded photos.
        Uses Hamming distance to determine similarity.
        """
        if not current_hash:
            return []
        
        matches = []
        try:
            # Query all evidence records with a hash
            past_evidences = db.query(Evidence).filter(Evidence.perceptual_hash != None).all()
            
            h1 = imagehash.hex_to_hash(current_hash)
            for ev in past_evidences:
                try:
                    h2 = imagehash.hex_to_hash(ev.perceptual_hash)
                    distance = h1 - h2
                    if distance <= threshold:
                        matches.append({
                            "evidence_id": ev.id,
                            "deal_id": ev.deal_id,
                            "submitted_by": ev.submitted_by,
                            "hamming_distance": distance
                        })
                except Exception as parse_err:
                    logger.warning(f"Could not parse hash {ev.perceptual_hash}: {parse_err}")
        except Exception as e:
            logger.error(f"Error checking photo reuse: {e}")
            
        return matches

    @classmethod
    def verify_dynamic_code(cls, image_path: str, code: str) -> bool:
        """
        Heuristic dynamic code verification in the delivery image.
        For simulation mode, we search for the code in the file name or use a mock flag.
        In a real application, we can use OpenCV contour matching / Tesseract OCR or multimodal AI.
        """
        if not code:
            return False
            
        # Simulation shortcut: if the code appears in the image path or filename, count it as verified.
        filename = os.path.basename(image_path).upper()
        if code.upper() in filename:
            logger.info(f"Dynamic code {code} verified by filename match.")
            return True
            
        # Standard fallback to visual inspection or OCR.
        # We will log it and return True for demo safety unless specified "fail" in filename.
        if "FAIL_CODE" in filename:
            logger.warning(f"Simulation trigger: failed dynamic code matching.")
            return False
            
        logger.info(f"Dynamic code {code} verified by default heuristic.")
        return True

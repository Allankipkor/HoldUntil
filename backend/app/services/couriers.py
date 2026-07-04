import logging

logger = logging.getLogger("courier_service")

def check_sendy(tracking_number: str) -> dict:
    """Sendy API lookup mock."""
    logger.info(f"Checking Sendy tracking: {tracking_number}")
    if tracking_number.startswith("SDY-") and not tracking_number.endswith("FAIL"):
        return {"status": "delivered", "delivered_at": "2026-07-04T12:00:00Z", "details": "Package picked up and delivered to buyer."}
    return {"status": "invalid", "details": "Tracking ID not found in Sendy database."}

def check_g4s(tracking_number: str) -> dict:
    """G4S Courier lookup mock."""
    logger.info(f"Checking G4S tracking: {tracking_number}")
    if tracking_number.startswith("G4S-") and not tracking_number.endswith("FAIL"):
        return {"status": "delivered", "delivered_at": "2026-07-04T13:30:00Z", "details": "Delivered to buyer G4S locker."}
    return {"status": "invalid", "details": "Tracking ID not found in G4S system."}

def check_boxleo(tracking_number: str) -> dict:
    """Boxleo tracking lookup mock."""
    logger.info(f"Checking Boxleo tracking: {tracking_number}")
    if tracking_number.startswith("BXL-") and not tracking_number.endswith("FAIL"):
        return {"status": "delivered", "delivered_at": "2026-07-04T14:15:00Z", "details": "Handed over to buyer directly."}
    return {"status": "invalid", "details": "Tracking ID not found in Boxleo system."}

def check_posta(tracking_number: str) -> dict:
    """Posta Kenya tracking lookup mock."""
    logger.info(f"Checking Posta Kenya tracking: {tracking_number}")
    if tracking_number.startswith("PST-") and not tracking_number.endswith("FAIL"):
        return {"status": "delivered", "delivered_at": "2026-07-04T10:00:00Z", "details": "Delivered to recipient postal address."}
    return {"status": "invalid", "details": "Tracking ID not found in Posta system."}

# Pluggable courier map
COURIERS = {
    "sendy": check_sendy,
    "g4s": check_g4s,
    "boxleo": check_boxleo,
    "posta": check_posta
}

def verify_tracking(courier_name: str, tracking_number: str) -> dict:
    """
    Main pluggable lookup router.
    Queries the appropriate function based on the courier name.
    """
    if not courier_name or not tracking_number:
        return {"status": "unknown", "details": "Missing courier name or tracking number."}
        
    courier_key = courier_name.lower().strip()
    if courier_key in COURIERS:
        try:
            return COURIERS[courier_key](tracking_number)
        except Exception as e:
            logger.error(f"Error checking tracking for {courier_name}: {e}")
            return {"status": "error", "details": f"Courier API check failed: {str(e)}"}
            
    return {"status": "unknown", "details": f"Courier {courier_name} not supported."}

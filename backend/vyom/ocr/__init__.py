"""OCR engine wrappers owned by P2."""


def available() -> bool:
    """Report whether the configured OCR engine can be initialized."""
    try:
        from .engine import RapidOcrEngine
        return RapidOcrEngine().available()
    except Exception:
        return False

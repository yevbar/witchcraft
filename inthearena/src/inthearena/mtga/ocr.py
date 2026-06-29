"""inthearena.mtga.ocr — read on-screen TEXT (card names) locally via the macOS Vision framework.

MTGA fans the hand at the bottom and overlaps the art, but each card's NAME banner stays legible. Reading the
names tells us WHICH card sits at which x — far more robust than detecting anonymous card rectangles and then
guessing the hand's sort order (the GRE hand-zone order does NOT match the on-screen left-to-right order). Vision
ships with macOS and runs locally in ~0.3s; nothing to install or download (pyobjc's Quartz/Foundation are
already pulled in by the screen-capture path).

Returns normalized, top-left-origin centers (0..1) so callers can map them onto a window rect regardless of
Retina scale. On non-macOS (or if Vision can't load) `recognize_text` returns [] and callers fall back.
"""

from __future__ import annotations

import io
import logging

_log = logging.getLogger(__name__)

_loaded = False


def _vision_classes():
    """Load the Vision framework once and return (VNImageRequestHandler, VNRecognizeTextRequest)."""
    global _loaded
    import objc
    from Foundation import NSBundle
    if not _loaded:
        if not NSBundle.bundleWithPath_("/System/Library/Frameworks/Vision.framework").load():
            raise RuntimeError("could not load Vision.framework")
        _loaded = True
    return objc.lookUpClass("VNImageRequestHandler"), objc.lookUpClass("VNRecognizeTextRequest")


def available() -> bool:
    """True if macOS Vision OCR can be used here. Lets a caller take a cheap OCR fast-path only where it works
    (macOS) and otherwise go straight to its fallback, instead of polling OCR-empty until a timeout."""
    try:
        _vision_classes()
        return True
    except Exception:
        return False


def recognize_text(image) -> list:
    """OCR a PIL `image`. Returns [(text, x_frac, y_frac)] — each recognized line's text and its NORMALIZED
    top-left-origin center (0..1). Returns [] if Vision is unavailable (non-macOS) or nothing is read."""
    if image is None:
        return []
    try:
        VNImageRequestHandler, VNRecognizeTextRequest = _vision_classes()
        import Quartz
        from Foundation import NSData
    except Exception as e:  # not macOS, or pyobjc/Vision missing
        _log.info("  ocr: Vision unavailable (%s: %s) — no text read", type(e).__name__, e)
        return []
    try:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        raw = buf.getvalue()
        data = NSData.dataWithBytes_length_(raw, len(raw))
        src = Quartz.CGImageSourceCreateWithData(data, None)
        cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None) if src else None
        if cg is None:
            return []
        req = VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(0)  # 0 = accurate (1 = fast); names are short, accuracy matters
        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
        handler.performRequests_error_([req], None)
        out = []
        for r in (req.results() or []):
            bb = r.boundingBox()  # normalized CGRect, origin BOTTOM-left
            xf = bb.origin.x + bb.size.width / 2.0
            yf = 1.0 - (bb.origin.y + bb.size.height / 2.0)  # -> top-left origin
            out.append((str(r.text()), float(xf), float(yf)))
        return out
    except Exception as e:
        _log.info("  ocr: recognition failed (%s: %s)", type(e).__name__, e)
        return []

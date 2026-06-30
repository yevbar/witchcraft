"""inthearena.mtga.vision — locate a UI element on screen with a small, locally-runnable vision model.

So click targets come from what's ACTUALLY on screen, not hardcoded coordinate estimates. `MoondreamLocator`
implements the `ElementLocator` seam (navigate.py) with Moondream — a ~2B vision model that runs locally and
can POINT at / DETECT an object from a text query ("Play button"), returning its location. We turn that into a
bounding box in the actuator's click-coordinate space.

Opt-in (`pip install inthearena[vision]`), lazy import — the base package never pulls the model. Pluggable: any
`ElementLocator` works, and passing none falls back to the coarse anchor. Note: on a HiDPI/Retina display the
screenshot is larger than the logical click coordinates; pass `scale` (e.g. 0.5) so located pixels map back to
click coordinates, or pass a `screen_size` to derive it from the image.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .navigate import Rect

_log = logging.getLogger("inthearena.mtga.vision")


def load_moondream(*, revision: str = "2025-06-21", device: Optional[str] = None):
    """Load Moondream LOCALLY (vikhyatk/moondream2 via transformers) — a ~3.7 GB download on first use, then it
    runs on CPU/MPS. Returns a model exposing `.point(image, query)` / `.detect(image, query)`. (The `moondream`
    pip package's `vl()` is cloud-first; this is the offline path.)"""
    import torch
    from transformers import AutoModelForCausalLM
    if device is None:
        device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    model = AutoModelForCausalLM.from_pretrained(
        "vikhyatk/moondream2", revision=revision, trust_remote_code=True)
    try:
        model = model.to(device)
    except Exception:
        pass
    return model


class MoondreamLocator:
    """Find a UI element with Moondream's point/detect. Pass a ready `model` (anything exposing `.point`/
    `.detect` — a local moondream2 from `load_moondream()`, or a cloud `moondream.vl(api_key=...)` handle); with
    no model and `local=True` it loads moondream2 locally (the default — keeps the screen on your machine).
    `scale` maps image pixels back to click coordinates (set ~0.5 on a 2x Retina display); or pass
    `screen_size=(w, h)` in click coords and the scale is derived from each screenshot's size. `origin` is added
    AFTER scaling — set it to a captured region's top-left (in click coords) when the image isn't the full
    screen, so returned boxes come back in absolute cursor coordinates."""

    def __init__(self, model=None, *, local: bool = True, api_key: Optional[str] = None,
                 scale: float = 1.0, screen_size: Optional[tuple] = None, origin: tuple = (0, 0)):
        if model is None:
            if api_key and not local:
                import moondream as md                      # cloud (sends the image off-machine)
                model = md.vl(api_key=api_key)
            else:
                model = load_moondream()                    # local, offline
        self._model = model
        self._scale = scale
        self._screen_size = screen_size
        self._ox, self._oy = origin

    def _scale_for(self, image) -> float:
        if self._screen_size is not None:                  # derive scale from image-px vs click-coord width
            iw = image.size[0]
            return (self._screen_size[0] / iw) if iw else 1.0
        return self._scale

    @staticmethod
    def _crop(image, region):
        """Crop `image` to a normalized (left, top, right, bottom) `region` of the FULL image. Returns
        (cropped_image, off_x_px, off_y_px) — the crop's top-left in FULL-image pixels, so detections can be
        mapped back. `region=None` -> the full image, offset (0, 0). Cropping shrinks the model's input
        dramatically (a button corner / the hand band is a fraction of the window), which is the main cost."""
        if region is None:
            return image, 0, 0
        iw, ih = image.size
        l, t, r, b = region
        box = (max(0, int(l * iw)), max(0, int(t * ih)), min(iw, int(r * iw)), min(ih, int(b * ih)))
        if box[2] <= box[0] or box[3] <= box[1]:           # degenerate -> fall back to the full image
            return image, 0, 0
        return image.crop(box), box[0], box[1]

    def locate(self, image, query: str, *, region=None) -> Optional[Rect]:
        """The on-screen bounding box of the element matching `query`, in click coordinates, or None. Tries
        `detect` (a box) first, then `point` (a center, wrapped in a small box). With `region` (a normalized
        crop of the window) the model only sees that slice — far fewer pixels, so much faster — and detections
        are mapped back through the crop offset."""
        sc = self._scale_for(image)                        # scale from the FULL image (region-independent)
        img, ox, oy = self._crop(image, region)
        iw, ih = img.size
        t0 = time.monotonic()

        objects = []
        try:
            objects = (self._model.detect(img, query) or {}).get("objects") or []
        except Exception:
            objects = []
        if objects:
            o = objects[0]
            x0, y0 = ox + o["x_min"] * iw, oy + o["y_min"] * ih
            x1, y1 = ox + o["x_max"] * iw, oy + o["y_max"] * ih
            _log.info("  vision: detect %r -> box in %.2fs%s", query, time.monotonic() - t0,
                      " (cropped)" if region is not None else "")
            return Rect(int(self._ox + x0 * sc), int(self._oy + y0 * sc),
                        int((x1 - x0) * sc), int((y1 - y0) * sc))

        try:
            points = (self._model.point(img, query) or {}).get("points") or []
        except Exception:
            points = []
        _log.info("  vision: detect+point %r -> %s in %.2fs%s", query, "hit" if points else "MISS",
                  time.monotonic() - t0, " (cropped)" if region is not None else "")
        if points:
            cx, cy = self._ox + (ox + points[0]["x"] * iw) * sc, self._oy + (oy + points[0]["y"] * ih) * sc
            r = 24                                          # no extent from a point — assume a small button area
            return Rect(int(cx - r), int(cy - r), 2 * r, 2 * r)
        return None

    def locate_all(self, image, query: str, *, region=None) -> list:
        """EVERY detected box for `query` (not just the first), in click coordinates — for repeated objects like
        the cards in hand. `detect` only; returns [] if none. `region` crops the input first (e.g. the hand band)
        so the model runs on a fraction of the pixels."""
        sc = self._scale_for(image)
        img, ox, oy = self._crop(image, region)
        iw, ih = img.size
        t0 = time.monotonic()
        try:
            objects = (self._model.detect(img, query) or {}).get("objects") or []
        except Exception:
            return []
        _log.info("  vision: detect-all %r -> %d box(es) in %.2fs%s", query, len(objects),
                  time.monotonic() - t0, " (cropped)" if region is not None else "")
        out = []
        for o in objects:
            x0, y0 = ox + o["x_min"] * iw, oy + o["y_min"] * ih
            x1, y1 = ox + o["x_max"] * iw, oy + o["y_max"] * ih
            out.append(Rect(int(self._ox + x0 * sc), int(self._oy + y0 * sc),
                            int((x1 - x0) * sc), int((y1 - y0) * sc)))
        return out

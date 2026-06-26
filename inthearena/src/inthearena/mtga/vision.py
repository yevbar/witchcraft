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

from typing import Optional

from .navigate import Rect


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

    def locate(self, image, query: str) -> Optional[Rect]:
        """The on-screen bounding box of the element matching `query`, in click coordinates, or None. Tries
        `detect` (a box) first, then `point` (a center, wrapped in a small box)."""
        iw, ih = image.size
        sc = self._scale_for(image)

        objects = []
        try:
            objects = (self._model.detect(image, query) or {}).get("objects") or []
        except Exception:
            objects = []
        if objects:
            o = objects[0]
            x0, y0, x1, y1 = o["x_min"] * iw, o["y_min"] * ih, o["x_max"] * iw, o["y_max"] * ih
            return Rect(int(self._ox + x0 * sc), int(self._oy + y0 * sc),
                        int((x1 - x0) * sc), int((y1 - y0) * sc))

        try:
            points = (self._model.point(image, query) or {}).get("points") or []
        except Exception:
            points = []
        if points:
            cx, cy = self._ox + points[0]["x"] * iw * sc, self._oy + points[0]["y"] * ih * sc
            r = 24                                          # no extent from a point — assume a small button area
            return Rect(int(cx - r), int(cy - r), 2 * r, 2 * r)
        return None

    def locate_all(self, image, query: str) -> list:
        """EVERY detected box for `query` (not just the first), in click coordinates — for repeated objects like
        the cards in hand. `detect` only; returns [] if none."""
        iw, ih = image.size
        sc = self._scale_for(image)
        try:
            objects = (self._model.detect(image, query) or {}).get("objects") or []
        except Exception:
            return []
        out = []
        for o in objects:
            x0, y0, x1, y1 = o["x_min"] * iw, o["y_min"] * ih, o["x_max"] * iw, o["y_max"] * ih
            out.append(Rect(int(self._ox + x0 * sc), int(self._oy + y0 * sc),
                            int((x1 - x0) * sc), int((y1 - y0) * sc)))
        return out

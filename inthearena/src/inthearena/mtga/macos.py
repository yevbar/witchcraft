"""macos.py — find the MTGA window on whichever monitor it lives on, and capture just that region.

The live actuator drives the cursor in macOS *global points* (main display's top-left = origin; other
monitors sit at their arranged offsets, which can be negative). A vision screenshot, by contrast, comes back in
*pixels* at the display's backing scale (2x on Retina). This module bridges the two so navigation works no
matter which monitor MTGA is on:

    win   = find_mtga_window()            # Rect in global points
    scale = display_scale(win)            # backing scale of that monitor (1.0 or 2.0)
    image = capture_rect(win)             # PIL image of just MTGA, in pixels
    # a located box's pixels map back to a cursor point as:  point = win.origin + pixel / scale
    locator  = MoondreamLocator(origin=(win.x, win.y), scale=1.0 / scale)
    actuator = PyAutoGuiActuator(rect=win, capture=lambda: capture_rect(win))

macOS only (uses Quartz + `screencapture`). Returns None / 1.0 gracefully when Quartz isn't available.
"""

from __future__ import annotations

import subprocess
import tempfile
from typing import Optional

from .navigate import Rect


def find_mtga_window(names=("MTGA", "Arena")) -> Optional[Rect]:
    """The MTGA game window's bounds in global points, or None if not found. Picks the LARGEST on-screen window
    owned by a matching app — MTGA also has a tiny title-strip window we must skip."""
    try:
        import Quartz
    except Exception:
        return None
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []
    best, best_area = None, 0
    for w in wins:
        owner = w.get("kCGWindowOwnerName", "") or ""
        if not any(n.lower() in owner.lower() for n in names):
            continue
        b = w.get("kCGWindowBounds") or {}
        r = Rect(int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0)))
        area = r.w * r.h
        if area > best_area:
            best, best_area = r, area
    return best


def display_scale(rect: Rect) -> float:
    """Backing scale (pixels per point) of the monitor containing `rect`'s center — 2.0 on Retina, else 1.0.
    Used to convert capture pixels back to cursor points. Falls back to 1.0 if it can't be determined."""
    try:
        import Quartz
    except Exception:
        return 1.0
    cx, cy = rect.x + rect.w / 2.0, rect.y + rect.h / 2.0
    err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
    for d in (ids or []):
        bb = Quartz.CGDisplayBounds(d)
        if (bb.origin.x <= cx < bb.origin.x + bb.size.width
                and bb.origin.y <= cy < bb.origin.y + bb.size.height):
            mode = Quartz.CGDisplayCopyDisplayMode(d)
            w_pts = bb.size.width or 1.0
            return Quartz.CGDisplayModeGetPixelWidth(mode) / w_pts
    return 1.0


def capture_rect(rect: Rect):
    """A PIL image of the screen region `rect` (global points), captured with `screencapture -R`. The image is
    in pixels at the monitor's backing scale, so it works on Retina and non-Retina alike."""
    from PIL import Image
    path = tempfile.mktemp(suffix=".png")                   # noqa: S306 - throwaway capture file
    subprocess.run(["screencapture", "-x", f"-R{rect.x},{rect.y},{rect.w},{rect.h}", path], check=True)
    return Image.open(path).convert("RGB")

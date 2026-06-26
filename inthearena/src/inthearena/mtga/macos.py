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


def mtga_pid(names=("MTGA", "Arena")) -> Optional[int]:
    """The MTGA process id (for posting events straight to it), or None."""
    try:
        import Quartz
    except Exception:
        return None
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    for w in wins:
        owner = w.get("kCGWindowOwnerName", "") or ""
        if any(n.lower() in owner.lower() for n in names):
            return w.get("kCGWindowOwnerPID")
    return None


# ── click backends ───────────────────────────────────────────────────────────────────────────────────────
# (x, y) are global points. pyautogui's plain click is an instantaneous down+up that Unity clients like MTGA
# often drop; these are progressively more forceful ways to land a real click. Each holds the button briefly.

def click_applescript(x: int, y: int) -> None:
    """Click via AppleScript (System Events). Needs Accessibility for the controlling app; least reliable for
    games (no AX hierarchy), but cheap to try."""
    subprocess.run(["osascript", "-e",
                    f'tell application "System Events" to click at {{{int(x)}, {int(y)}}}'], check=False)


def click_quartz(x: int, y: int, *, hold: float = 0.10, pid: Optional[int] = None) -> None:
    """Click via Quartz CGEvents: a mouse-moved, then left-down, hold, left-up. Uses a shared HID event source
    and sets the clickState field (1) — both of which some apps require to recognize a real click. With `pid`
    the events are posted STRAIGHT TO that process (CGEventPostToPid)."""
    import time
    import Quartz
    pt = Quartz.CGPointMake(float(x), float(y))
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)

    def _ev(kind, click_state=0):
        e = Quartz.CGEventCreateMouseEvent(src, kind, pt, Quartz.kCGMouseButtonLeft)
        if click_state:
            Quartz.CGEventSetIntegerValueField(e, Quartz.kCGMouseEventClickState, click_state)
        return e

    def _post(ev):
        if pid:
            Quartz.CGEventPostToPid(pid, ev)
        else:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    _post(_ev(Quartz.kCGEventMouseMoved))
    time.sleep(0.02)
    _post(_ev(Quartz.kCGEventLeftMouseDown, 1))
    time.sleep(hold)
    _post(_ev(Quartz.kCGEventLeftMouseUp, 1))


_NX_LMOUSEDOWN, _NX_LMOUSEUP, _NX_MOUSEMOVED, _NX_VER = 1, 2, 5, 2


def _iohid_open():
    """Open an IOHIDSystem param connection for IOHIDPostEvent. Returns (iokit, connect, IOGPoint, NXMouseData,
    open_rc); connect is None if the open failed."""
    import ctypes
    import ctypes.util

    iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))

    class IOGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int16), ("y", ctypes.c_int16)]

    class NXMouseData(ctypes.Structure):                    # the mouse arm of the NXEventData union (+ padding)
        _fields_ = [("subx", ctypes.c_int16), ("suby", ctypes.c_int16), ("eventNum", ctypes.c_int16),
                    ("click", ctypes.c_int32), ("pressure", ctypes.c_uint8), ("buttonNumber", ctypes.c_uint8),
                    ("subType", ctypes.c_uint8), ("reserved2", ctypes.c_uint8), ("reserved3", ctypes.c_int32),
                    ("pad", ctypes.c_uint8 * 100)]          # over-allocate to cover the full union (tablet, etc.)

    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    iokit.IOServiceOpen.restype = ctypes.c_int
    iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_uint32)]
    iokit.IOHIDPostEvent.restype = ctypes.c_int
    iokit.IOHIDPostEvent.argtypes = [ctypes.c_uint32, ctypes.c_uint32, IOGPoint, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]

    task = ctypes.c_uint32.in_dll(ctypes.CDLL(None), "mach_task_self_").value
    service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"IOHIDSystem"))
    connect = ctypes.c_uint32(0)
    rc = iokit.IOServiceOpen(service, task, 1, ctypes.byref(connect))        # kIOHIDParamConnectType = 1
    return iokit, (connect.value if rc == 0 else None), IOGPoint, NXMouseData, rc & 0xFFFFFFFF


def iohid_move(x: int, y: int) -> dict:
    """Post a real NX_MOUSEMOVED via IOHIDPostEvent so the CLIENT's internal pointer tracks to (x, y) (MTGA's
    hover lights up). pyautogui's warp doesn't do this, which is why chained clicks used to miss."""
    import ctypes
    iokit, connect, IOGPoint, NXMouseData, rc = _iohid_open()
    if connect is None:
        return {"open": rc}
    md = NXMouseData()
    code = iokit.IOHIDPostEvent(connect, _NX_MOUSEMOVED, IOGPoint(int(x), int(y)), ctypes.byref(md),
                                _NX_VER, 0, 0)
    return {"open": rc, "move": code & 0xFFFFFFFF}


def click_iohid(x: int, y: int, *, hold: float = 0.10) -> dict:
    """Click via the legacy IOKit path: IOHIDPostEvent NX move + down/up. Returns IOKit status codes ({'open': 0,
    'down': 0, 'up': 0} = each call accepted). The move reaches MTGA (hover), but the button events may not —
    if so, chain a different click backend after iohid_move()."""
    import ctypes
    import time
    iokit, connect, IOGPoint, NXMouseData, rc = _iohid_open()
    out = {"open": rc}
    if connect is None:
        return out
    loc = IOGPoint(int(x), int(y))
    md = NXMouseData(); md.click = 1; md.pressure = 0
    iokit.IOHIDPostEvent(connect, _NX_MOUSEMOVED, loc, ctypes.byref(md), _NX_VER, 0, 0)
    time.sleep(0.02)
    md.pressure = 255
    out["down"] = iokit.IOHIDPostEvent(connect, _NX_LMOUSEDOWN, loc, ctypes.byref(md), _NX_VER, 0, 0) & 0xFFFFFFFF
    time.sleep(hold)
    md.pressure = 0
    out["up"] = iokit.IOHIDPostEvent(connect, _NX_LMOUSEUP, loc, ctypes.byref(md), _NX_VER, 0, 0) & 0xFFFFFFFF
    return out


def activate_app(pid: int) -> bool:
    """Bring the app with `pid` to the front (frontmost/active). Some clients ignore clicks while backgrounded.
    Returns True on success."""
    try:
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
    except Exception:
        return False
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))


def activate_app_applescript(name: str = "MTGA") -> bool:
    """Bring the app `name` to the front via AppleScript (System Events). Returns True if osascript succeeded."""
    script = f'tell application "System Events" to set frontmost of (first process whose name is "{name}") to true'
    return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0

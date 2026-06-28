"""inthearena.mtga.live — follow Player.log AS IT IS WRITTEN (tail -f) and keep state accurately updated.

`iter_decisions` / `latest_view` replay a finished log; this follows a live one. `tail_lines` yields each new
complete line as MTGA appends it (buffering partial writes, and resetting if the file is truncated/rotated on a
client restart). `LiveState` ingests those lines and keeps an always-current picture:
  * `view`         — the diff-tracked `GameView` (board / life / turn), advanced by each GRE frame
  * `current_view` — the recognized client view (HOME / PLAY_MENU / GAMEPLAY / …), from scene + match-state lines
`follow()` drives a `LiveState` and yields a `Decision` the moment the GRE asks the local player to act — so a
policy can react live (`d.view` is the live board; the `LiveState` carries `current_view`).

Read-only: it only reads the log. Default-follows from the start so no in-progress match is missed.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .gre import DEFAULT_LOG, Decision, GameView, parse_line, update
from .views import RecognizedViews, from_scene_name

_DECODER = json.JSONDecoder()
_GAME_PLAYING = "MatchGameRoomStateType_Playing"
_GAME_DONE = "MatchGameRoomStateType_MatchCompleted"


def gre_advanced(path: str, baseline_size: int, *, timeout: float = 4.0, poll: float = 0.2) -> bool:
    """Did the GRE write a new server message past `baseline_size` within `timeout`? That's GROUND TRUTH that a
    just-performed action REGISTERED and the game advanced — a dropped click leaves the log silent (the server
    received nothing). Used to decide whether to RETRY an action, instead of reading the screen (which a
    lock-screen or mid-animation frame can fool). Returns as soon as a new 'GreToClient' message appears."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if os.path.getsize(path) > baseline_size:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    fh.seek(baseline_size)
                    if "GreToClient" in fh.read():        # the server responded to our action -> it took effect
                        return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def tail_lines(path: str = DEFAULT_LOG, *, from_start: bool = True, poll: float = 0.5,
               stop: Optional[Callable[[], bool]] = None, start_offset: Optional[int] = None) -> Iterator[str]:
    """Follow `path`, yielding each complete line as it appears. Reads existing content first (unless
    `from_start=False`, which starts at EOF), then polls for appends every `poll`s. `start_offset` (a byte
    offset) overrides both: resume reading from EXACTLY there — so a caller that already drained the existing
    content can continue with NO gap (from_start=False seeks to the live EOF, which would drop anything written
    in between). Partial (un-newlined) writes are buffered until complete; if the file shrinks (truncated or
    rotated on a client restart) it reopens from the top. `stop()` (optional) ends the otherwise-infinite
    follow; closing the generator also cleans up."""
    fh = open(path, encoding="utf-8", errors="replace")
    try:
        if start_offset is not None:
            fh.seek(start_offset)
        elif not from_start:
            fh.seek(0, os.SEEK_END)
        buf = ""
        while stop is None or not stop():
            chunk = fh.read()
            if chunk:
                buf += chunk
                nl = buf.find("\n")
                while nl >= 0:
                    yield buf[:nl]
                    buf = buf[nl + 1:]
                    nl = buf.find("\n")
                continue
            try:
                if os.path.getsize(path) < fh.tell():       # truncated / rotated -> restart from the top
                    fh.close()
                    fh = open(path, encoding="utf-8", errors="replace")
                    buf = ""
                    continue
            except OSError:
                pass
            time.sleep(poll)
    finally:
        fh.close()


def tail_messages(path: str = DEFAULT_LOG, **kw) -> Iterator:
    """Follow the log and yield each typed `GreMessage` as it is written."""
    for line in tail_lines(path, **kw):
        yield from parse_line(line)


@dataclass
class LiveState:
    """An always-current picture, fed one log line at a time: the diff-tracked `view` (board) and the
    recognized `current_view` (client screen). Drive it with `follow()`, or feed lines yourself."""

    view: GameView = field(default_factory=GameView)
    current_view: Optional[RecognizedViews] = None
    match_over: bool = False                                 # the live match has ended (post-game overlays pending)

    def feed_line(self, line: str) -> list:
        """Apply one raw log line. Updates `current_view` (scene / match-state signals) and `view` (GRE
        frames); returns any `Decision`s the line raised for the local player (usually 0 or 1)."""
        if _GAME_PLAYING in line:                            # a match went live
            self.current_view = RecognizedViews.GAMEPLAY
            self.match_over = False
        elif _GAME_DONE in line:                             # match over -> menu pending
            self.current_view = None
            self.match_over = True
        i = line.find("SceneChange ")
        if i >= 0:
            b = line.find("{", i)
            if b >= 0:
                try:
                    obj, _ = _DECODER.raw_decode(line[b:])
                except ValueError:
                    obj = None
                if obj and obj.get("toSceneName"):
                    self.current_view = from_scene_name(obj["toSceneName"])   # may be None for an unmodeled scene

        out = []
        for m in parse_line(line):
            d = update(self.view, m)
            if d is not None:
                out.append(d)
        return out


def follow(path: str = DEFAULT_LOG, *, state: Optional[LiveState] = None, from_start: bool = True,
           poll: float = 0.5, stop: Optional[Callable[[], bool]] = None,
           start_offset: Optional[int] = None) -> Iterator[Decision]:
    """Tail the live log and yield a `Decision` each time the GRE asks the local player to act. Pass a
    `LiveState` to read `state.view` / `state.current_view` between decisions; one is created if omitted.
    `start_offset` resumes from a byte offset (where a prior drain stopped) so no decision is skipped."""
    st = state if state is not None else LiveState()
    for line in tail_lines(path, from_start=from_start, poll=poll, stop=stop, start_offset=start_offset):
        for d in st.feed_line(line):
            yield d

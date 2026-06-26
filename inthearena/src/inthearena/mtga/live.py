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


def tail_lines(path: str = DEFAULT_LOG, *, from_start: bool = True, poll: float = 0.5,
               stop: Optional[Callable[[], bool]] = None) -> Iterator[str]:
    """Follow `path`, yielding each complete line as it appears. Reads existing content first (unless
    `from_start=False`, which starts at EOF), then polls for appends every `poll`s. Partial (un-newlined)
    writes are buffered until complete; if the file shrinks (truncated or rotated on a client restart) it
    reopens from the top. `stop()` (optional) ends the otherwise-infinite follow; closing the generator also
    cleans up."""
    fh = open(path, encoding="utf-8", errors="replace")
    try:
        if not from_start:
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

    def feed_line(self, line: str) -> list:
        """Apply one raw log line. Updates `current_view` (scene / match-state signals) and `view` (GRE
        frames); returns any `Decision`s the line raised for the local player (usually 0 or 1)."""
        if _GAME_PLAYING in line:                            # a match went live
            self.current_view = RecognizedViews.GAMEPLAY
        elif _GAME_DONE in line:                             # match over -> menu pending
            self.current_view = None
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
           poll: float = 0.5, stop: Optional[Callable[[], bool]] = None) -> Iterator[Decision]:
    """Tail the live log and yield a `Decision` each time the GRE asks the local player to act. Pass a
    `LiveState` to read `state.view` / `state.current_view` between decisions; one is created if omitted."""
    st = state if state is not None else LiveState()
    for line in tail_lines(path, from_start=from_start, poll=poll, stop=stop):
        for d in st.feed_line(line):
            yield d

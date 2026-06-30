"""mtg._native — locate a PREBUILT compiled engine artifact from a configurable path.

The engine backends normally compile datalog/engine_rules.dl on demand (souffle codegen -> clang/g++)
and cache the result in tempdir. To run from a shipped or hand-placed binary instead — no local C++
toolchain, no compile, "works right away" — they consult these resolvers FIRST:

    .so  (engine_inproc, in-process):  $MTG_ENGINE_SO  -> mtg/_native/<tag>/libmtg_engine.so
                                                       -> mtg/_native/libmtg_engine.so   -> None
    exe  (engine_native, subprocess):  $MTG_ENGINE_BIN -> mtg/_native/<tag>/mtg_engine
                                                       -> mtg/_native/mtg_engine          -> None

None means "no prebuilt found — compile on demand" (the current dev behavior, unchanged).

<tag> is `<sys.platform>_<machine>` (e.g. darwin_arm64, linux_x86_64) so a future per-arch wheel can
drop the right binary in place; an untagged file is also honored for quick local use.

CONTRACT: a prebuilt artifact must be built from the SAME engine_rules.dl it is loaded against. The
in-process .so additionally registers a ProgramFactory whose name is keyed to that file's content, so a
mismatched .so is detected at load and ignored (the caller falls back to compiling / other backends).
The distribution wheel will ship engine_rules.dl and a per-arch binary built together, so they match.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "_native"   # packages/mtg/_native/


def platform_tag() -> str:
    """e.g. 'darwin_arm64', 'linux_x86_64' — how per-arch prebuilt binaries are namespaced."""
    return f"{sys.platform}_{platform.machine()}"


def _resolve(env_var: str, filename: str) -> Path | None:
    override = os.environ.get(env_var)
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    for cand in (_DIR / platform_tag() / filename, _DIR / filename):
        if cand.exists():
            return cand
    return None


def engine_so() -> Path | None:
    """Prebuilt in-process engine .so, or None to compile on demand."""
    return _resolve("MTG_ENGINE_SO", "libmtg_engine.so")


def engine_bin() -> Path | None:
    """Prebuilt native engine executable, or None to compile on demand."""
    return _resolve("MTG_ENGINE_BIN", "mtg_engine")

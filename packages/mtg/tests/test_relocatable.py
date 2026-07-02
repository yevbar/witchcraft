"""test_relocatable.py — guard the packaging invariant that makes `pip install python-mtg` work off the
repo: every runtime artifact (the Datalog build, the oracle corpus) is addressed through mtg._paths, NOT a
cwd-relative or repo-root-hardcoded path. A regression here breaks the WHEEL while dev (run from the repo)
keeps working — so it's exactly the kind of bug a normal test run misses. This catches it statically.

Run: MTG_NO_SPACY=1 python3 packages/mtg/tests/test_relocatable.py  (from the repo root)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ROOT = Path(_r)
PKG = ROOT / "packages" / "mtg"
CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


# patterns that reintroduce a non-relocatable path to the datalog build (the wheel bundles it as mtg/_datalog)
_FORBIDDEN = [
    re.compile(r'Path\(\s*["\']datalog/'),                     # cwd-relative Path("datalog/…")
    re.compile(r'parent\.parent\.parent\s*/\s*["\']datalog["\']'),   # repo-root-relative via __file__
    re.compile(r'parents\[3\]\s*/\s*["\']datalog["\']'),
]


def _no_hardcoded_datalog_paths() -> None:
    """No CORE module (excluding the resolver itself + tests) may hardcode a datalog path."""
    offenders = []
    for py in list(PKG.glob("*.py")) + list((PKG / "engine").glob("*.py")):
        if py.name == "_paths.py":
            continue
        text = py.read_text(encoding="utf-8")
        for pat in _FORBIDDEN:
            if pat.search(text):
                offenders.append(f"{py.relative_to(ROOT)}: {pat.pattern}")
    check("no core module hardcodes a datalog path (use mtg._paths)", not offenders)
    if offenders:
        print("  offenders:", offenders)


def _paths_resolve() -> None:
    from mtg import _paths
    check("datalog_dir() exists", _paths.datalog_dir().is_dir())
    check("engine_rules.dl resolves", _paths.datalog("engine_rules.dl").exists())
    check("corpus resolves", _paths.corpus_path().exists())


def _env_override_wins() -> None:
    """$MTG_DATALOG must take precedence — this is how a user points at a custom build (and how the wheel's
    bundled dir is overridable)."""
    from mtg import _paths
    os.environ["MTG_DATALOG"] = "/some/custom/dir"
    try:
        check("MTG_DATALOG overrides the datalog dir", str(_paths.datalog_dir()) == "/some/custom/dir")
    finally:
        del os.environ["MTG_DATALOG"]


def _incremental_imports_without_harness() -> None:
    """engine_incremental must import even when the fork's `harness` bridge is absent (the wheel case),
    degrading to available()==False rather than an ImportError."""
    import importlib
    m = importlib.import_module("mtg.engine_incremental")
    check("engine_incremental imports without eager `harness`", m.harness is None)
    check("engine_incremental.available() is callable (no crash)", isinstance(m.available(), bool))


def run() -> None:
    _no_hardcoded_datalog_paths()
    _paths_resolve()
    _env_override_wins()
    _incremental_imports_without_harness()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

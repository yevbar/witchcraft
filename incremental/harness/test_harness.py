"""Smoke test for the in-process incremental harness (Phase 3a foundation).

Verifies the plumbing BEFORE any `update` subroutine exists: compile a --incremental program, Bootstrap it
in-process, and confirm the resident relations match a from-scratch CLI run. No incremental behaviour yet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

TC = """\
.decl edge(a:number, b:number)
.input edge
.decl path(a:number, b:number)
.output path
path(x, y) :- edge(x, y).
path(x, y) :- path(x, z), edge(z, y).
"""

EDGES = {("1", "2"), ("2", "3"), ("1", "3")}
EXPECT_PATH = {("1", "2"), ("2", "3"), ("1", "3")}  # 1->2, 2->3, 1->3 (direct); no new transitives here


def main():
    if not harness.available():
        print("harness UNAVAILABLE (no fork souffle binary or no C++ compiler) — skipping")
        return 0
    h = harness.Harness(TC, incremental=True)
    h.bootstrap({"edge": EDGES})
    dump = h.dump()
    path = dump.get("path", set())
    edge = dump.get("edge", set())
    ok = True
    if edge != EDGES:
        print(f"FAIL: edge resident = {edge}, expected {EDGES}"); ok = False
    if path != EXPECT_PATH:
        print(f"FAIL: path resident = {path}, expected {EXPECT_PATH}"); ok = False
    # (the `update` subroutine does not exist yet — calling it would fatal-abort; added in the next step)
    print("HARNESS BOOTSTRAP:", "PASS ✓" if ok else "FAIL ✗")
    h.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

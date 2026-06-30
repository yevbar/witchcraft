"""bench_mtg.py — simulation-throughput benchmark for the mtg engine.

Measures how many game states the engine can simulate per second — the number that gates search depth.
Three primitives, slowest-meaningful-unit last:

  engine_native.evaluate(fkey)  the atomic "derive a state's consequences" (one native-binary call)
  driver.run(state, …) uncached the same via the driver (cache cleared each call so it really evaluates)
  env.step(state, action)       a full search-node expansion (legal_actions + apply; ~N evaluate calls)

Pair with forge_integration/ForgeBench.java for the Forge side. See SIMULATION_BENCHMARK.md for results +
the engineering analysis (where the time goes, and the levers to raise the number).

Run: python3 bench_mtg.py        (needs datalog/cards.dl + a souffle toolchain for the native binary)
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

from mtg import bridge_to_engine as bridge
from interpreter import card_corpus
from mtg import driver
from mtg import engine_native
from mtg import engine_inproc
from mtg.engine import env
from mtg import sim


def _rate(fn, warm: int = 3, secs: float = 6.0) -> float:
    """Calls/sec of `fn` over a `secs` window (after `warm` warmup calls)."""
    for _ in range(warm):
        fn()
    n, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < secs:
        fn()
        n += 1
    return n / (time.perf_counter() - t0)


def _report(label: str, rate: float) -> None:
    print(f"[mtg] {label:34} {rate:8.1f} states/sec  ->  1s={rate:.0f}  5s={5 * rate:.0f}  10s={10 * rate:.0f}")


def main() -> None:
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    # a realistic mid-game board: a few real permanents + lands per side (≈200 facts, comparable to ForgeBench).
    boards = {
        "alice": {"battlefield": ["Grizzly Bears", "Llanowar Elves", "Serra Angel", "Forest", "Forest", "Plains"], "library": 20},
        "bob": {"battlefield": ["Hill Giant", "Gray Ogre", "Mountain", "Mountain", "Island"], "library": 20},
    }
    state = bridge.make_state(boards)
    fkey = frozenset((rel, frozenset(rows)) for rel, rows in state.items() if rows)
    nfacts = sum(len(v) for v in state.values())
    print(f"state: {len(state)} relations, {nfacts} facts;  native_binary={engine_native.available()}\n")

    _report("native evaluate() [subprocess]", _rate(lambda: engine_native.evaluate(fkey)))
    if engine_inproc.available():                                # the in-process .so: no fork, no files
        _report("inproc evaluate() [in-process .so]", _rate(lambda: engine_inproc.evaluate(fkey)))

    def run_uncached():
        driver.clear_cache()
        driver.run(state, ["controls"])
    _report("driver.run (uncached)", _rate(run_uncached))

    acts = env.legal_actions(state)
    print(f"  (legal_actions returned {len(acts)} options for this state)")
    if acts:
        action = acts[0]

        def env_step():
            driver.clear_cache()
            env.step(state, action)
        _report("env.step (full node expansion)", _rate(env_step))


if __name__ == "__main__":
    main()

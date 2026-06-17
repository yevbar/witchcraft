"""test_eval_cache.py — the bounded-LRU eval cache (lever #2): the driver memoizes engine evaluations so a
search node's expansion amortizes its shared auto-advance phase crossings, while a cap keeps memory bounded
under arbitrarily long search. Asserts: (1) results are identical with and without a cap (eviction never
changes an answer), (2) the cache never exceeds its cap and evicts LRU, (3) a warm cache cuts actual engine
evals (cache misses) vs a cold one. Run: python3 test_eval_cache.py
"""
from __future__ import annotations

import contextlib
import io
import os

import driver
import env
import bridge_to_engine as bridge

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)


def _state():
    boards = {
        "alice": {"battlefield": ["Forest", "Forest", "Forest", "Mountain", "Mountain"],
                  "hand": ["Grizzly Bears", "Lightning Bolt", "Hill Giant", "Gray Ogre", "Giant Growth"],
                  "library": 20},
        "bob": {"battlefield": ["Llanowar Elves", "Serra Angel", "Plains", "Island"], "library": 20},
    }
    return env.start(bridge.make_state(boards))


def _expand(s0, acts):
    """Resulting states (as canonical fact keys) of expanding every child — the observable transition output."""
    outs = []
    with contextlib.redirect_stdout(io.StringIO()):
        for a in acts:
            outs.append(driver._facts_key(env.step(s0, a)))
    return outs


def run():
    s0 = _state()
    acts = env.legal_actions(s0)
    check("node has multiple legal actions to expand", len(acts) > 1)

    # (1) eviction never changes an answer: expand with a TINY cap vs unbounded, compare resulting states.
    driver._CACHE_MAX = 0; driver.clear_cache()
    big = _expand(s0, acts)
    driver._CACHE_MAX = 5; driver.clear_cache()
    small = _expand(s0, acts)
    driver._CACHE_MAX = 200000
    check("results identical with a tiny cap vs unbounded (eviction is transparent)", big == small)

    # (2) the cache stays within its cap and actually evicts.
    driver._CACHE_MAX = 20; driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(4):
            for a in acts:
                env.step(s0, a)
    st = driver.cache_stats()
    check("cache size never exceeds the cap", st["distinct_states"] <= 20)
    check("LRU eviction happened past the cap", st["evictions"] > 0)
    driver._CACHE_MAX = 200000

    # (3) a WARM cache cuts actual engine evals (cache misses) for a re-expansion vs a COLD one.
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):                # COLD: clear before each step
        cold_evals = 0
        for a in acts:
            driver.clear_cache()
            before = driver.cache_stats()["souffle_evals"]
            env.step(s0, a)
            cold_evals += driver.cache_stats()["souffle_evals"] - before
    driver.clear_cache()
    with contextlib.redirect_stdout(io.StringIO()):                # prime, then re-expand WARM
        for a in acts:
            env.step(s0, a)
        warm_before = driver.cache_stats()["souffle_evals"]
        for a in acts:
            env.step(s0, a)
        warm_evals = driver.cache_stats()["souffle_evals"] - warm_before
    check(f"warm re-expansion needs far fewer engine evals than cold ({warm_evals} < {cold_evals})",
          warm_evals < cold_evals)
    check("warm re-expansion is mostly cache hits (≤1 stray miss)", warm_evals <= 1)

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

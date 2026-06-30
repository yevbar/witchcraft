"""mtg.experiments._safe — process guards so an experiment can't take the laptop down.

Import this FIRST (before torch does any work) and call the guards in __main__ AND in every pool worker.
The historical restarts on this box were memory-pressure / compile storms under all-core oversubscription,
not a GPU hang (torch here is CPU-only). These guards remove that surface:

    import _safe
    _safe.clamp(mem_gb=6, cpu_s=120)     # 1 thread/process + address-space + CPU-seconds ceilings
    _safe.watchdog(90)                    # hard wall-clock kill (SIGALRM)
    _safe.rss_guard(8)                    # reliable RSS ceiling on macOS (RLIMIT_AS is best-effort there)

Sizing for this machine (24 GB, 14 cores): with mem_gb=6 and Pool<=2, workers stay well under RAM; a
runaway dies as ONE process (clean) instead of swap-deathing the box.
"""

from __future__ import annotations

import os
import resource
import signal
import sys
import threading
import time

# Pin BLAS/OpenMP threads before torch/numpy import them — kills the Pool(2)x14-core oversubscription.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# Bound the engine eval-cache (driver._CACHE). Its default cap is 200k distinct states, each holding ALL derived
# relations — a tree SEARCH visits ~500 distinct states/move across many moves/games, so the cache climbs to
# MULTIPLE GB per process (the lookahead-gauntlet near-swap-death). A cap of ~20k keeps within-search amortization
# (one search is <=node_budget distinct states) while plateauing memory at <~1GB/process. Override per-experiment
# (e.g. a memory-tight CONCURRENT fan-out can set MTG_EVAL_CACHE=8000 for ~0.5GB/worker).
os.environ.setdefault("MTG_EVAL_CACHE", "20000")

_IS_MAC = sys.platform == "darwin"


def clamp(mem_gb: float = 6.0, cpu_s: int | None = None) -> None:
    """Pin torch to 1 thread and cap this process's address space (best-effort on macOS) and, if given, its
    CPU-seconds. A runaway then dies as a clean per-process error rather than dragging the whole machine into
    swap. Safe to call once per process (main + each worker)."""
    try:
        soft = int(mem_gb * 1024 ** 3)
        resource.setrlimit(resource.RLIMIT_AS, (soft, soft))           # macOS often ignores this — rss_guard backs it up
    except (ValueError, OSError):
        pass
    if cpu_s:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_s), int(cpu_s)))   # enforced on macOS, raises SIGXCPU
        except (ValueError, OSError):
            pass
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:
        pass


def watchdog(wall_s: float) -> None:
    """Arm a hard wall-clock kill: after `wall_s` seconds the process exits non-zero. Bounds runaway
    games/search even if a single move hangs."""
    def _die(signum, frame):
        sys.stderr.write(f"\n[_safe] watchdog fired after {wall_s}s — killing process\n")
        sys.stderr.flush()
        os._exit(2)
    signal.signal(signal.SIGALRM, _die)
    signal.setitimer(signal.ITIMER_REAL, float(wall_s))


def rss_guard(mem_gb: float = 8.0, poll_s: float = 0.5) -> None:
    """A daemon thread that hard-exits if this process's peak RSS crosses `mem_gb`. This is the RELIABLE memory
    ceiling on macOS (where RLIMIT_AS is commonly a no-op). ru_maxrss is bytes on macOS, KiB on Linux."""
    cap = int(mem_gb * 1024 ** 3)
    scale = 1 if _IS_MAC else 1024                                     # ru_maxrss units differ by platform
    def _poll():
        while True:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
            if rss > cap:
                sys.stderr.write(f"\n[_safe] rss_guard fired: {rss/1024**3:.1f} GB > {mem_gb} GB — killing\n")
                sys.stderr.flush()
                os._exit(3)
            time.sleep(poll_s)
    threading.Thread(target=_poll, daemon=True).start()

"""inthearena.mtga.shadow — run a Policy over an MTGA log in SHADOW mode: read the GRE decision stream and,
at each point the client was asked to act, record what the policy WOULD do. No automation, no input — purely
reads the log the client already wrote. This is the research seam: replay your real games (or tail a live
one) and see what `AggroPolicy` (or any wired bot) would have chosen at every decision.

    python3 -m inthearena.mtga.shadow [LOG_PATH]      # defaults to the macOS Player.log

Driving the live client to actually PLAY (a pyautogui executor over these same choices) is a separate,
deliberate step — and botting the live MTGA client is against its Terms of Service, so this read-only shadow
analysis is the safe research mode.
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Iterator

from .gre import DEFAULT_LOG, Decision, iter_decisions
from .policy import AggroPolicy, Policy, describe


def replay(path: str, policy: Policy) -> Iterator[tuple[Decision, object]]:
    """Yield `(decision, choice)` for every GRE decision in the log, choice = what `policy` would do."""
    for d in iter_decisions(path):
        yield d, policy.decide(d)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else DEFAULT_LOG
    policy = AggroPolicy()
    kinds: Counter = Counter()
    n = 0
    print(f"shadowing {policy.name} over {path}\n", flush=True)
    for d, choice in replay(path, policy):
        kinds[d.kind] += 1
        n += 1
        # show the meatier decisions (skip the flood of trivial single-pass priority windows)
        if d.kind == "actions" and (not choice or choice.actionType == "ActionType_Pass"):
            continue
        print(f"  {d.view.phase:24s} seat{d.seat}  {d.kind:9s} ({len(d.options)} opts)  ->  {describe(d, choice)}")
    print(f"\n{n} decisions seen: " + ", ".join(f"{k}={c}" for k, c in kinds.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

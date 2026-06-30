"""Build datalog/stack.dl — actions that DON'T use the stack, interpreted from rules.txt.

A recurring formula across the rulebook closes a rule by declaring that the action it
just described bypasses the stack:

    "This turn-based action doesn't use the stack."          (502.2, 505.4, 904.9, …)
    "This is a special action ... it doesn't use the stack." (701.40b, 702.37e, …)
    "This action doesn't use the stack."                     (505.6b)

The "...doesn't use the stack" clause is the fixed anchor; the action's KIND is read
from the same sentence (turn-based / special / state-based action, else a generic
action). Each match -> skips_stack(rule, kind). Abstains when no anchor sentence exists,
and stays generic ("action") rather than guess a kind the sentence doesn't state.

(State-based actions that skip the stack, e.g. §704, are mostly stated with the same
clause; the §714 Saga ones are already interpreted by build_saga, so they don't recur here.)
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_ANCHOR = "doesn't use the stack"
# kind keyword (as it appears in the sentence) -> relation value, in priority order.
_KINDS = [
    ("turn-based action", "turn_based_action"),
    ("special action", "special_action"),
    ("state-based action", "state_based_action"),
]
_SENT = re.compile(r"(?<=[.])\s+")


def _norm(text: str) -> str:
    return text.replace("’", "'").replace("“", '"').replace("”", '"')


def skips_stack() -> list[tuple[str, str]]:
    """(rule, kind) for every rule whose prose declares an action that skips the stack."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    norm = _norm(sr.text)
                    for sent in _SENT.split(norm):
                        if _ANCHOR not in sent:
                            continue
                        low = sent.lower()
                        kind = "action"
                        for kw, val in _KINDS:
                            if kw in low:
                                kind = val
                                break
                        key = (sr.number, kind)
                        if key not in seen:
                            seen.add(key)
                            out.append(key)
    return out


def build() -> tuple[str, dict]:
    rows = skips_stack()
    by_kind = {}
    for _n, k in rows:
        by_kind[k] = by_kind.get(k, 0) + 1

    p = Program()
    p.comment("stack.dl — actions that DON'T use the stack, interpreted from rules.txt.")
    p.comment("skips_stack(rule, kind); kind = turn_based_action | special_action | "
              "state_based_action | action. GENERATED.")
    p.blank()
    p.decl("skips_stack", [("rule", "symbol"), ("kind", "symbol")])
    p.blank()
    for num, kind in rows:
        p.fact(f'skips_stack("{num}", "{kind}")')
    p.blank()
    p.output("skips_stack")
    p.blank()
    p.comment("conformance — spot-check the stack-skipping actions the rules state plainly")
    p.conformance(
        [("expect_skips", [("rule", "symbol"), ("kind", "symbol")])],
        [("skips", "expect_skips(R, K)", "miss", "skips_stack(R, K)")],
    )
    for atom in ['expect_skips("502.2", "turn_based_action")',
                 'expect_skips("701.18a", "special_action")',
                 'expect_skips("505.6b", "action")']:
        p.fact(atom)
    return p.text(), {"total": len(rows), "by_kind": by_kind}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/stack.dl").write_text(source, encoding="utf-8")
    kinds = ", ".join(f"{k}={v}" for k, v in sorted(report["by_kind"].items()))
    print(f"wrote datalog/stack.dl ({report['total']} skips_stack: {kinds})")


if __name__ == "__main__":
    main()

"""Build datalog/ending.dl — the §104 win / lose / draw conditions, from rules.txt.

§104 states how a game ends, in a regular frame:

  "A player ... wins the game if [condition]."
  "If [condition], that player loses the game."
  "If [condition], the game is a draw."

       -> game_end(outcome, subject, condition)    outcome = win | lose | draw

The outcome and subject (player / team) are read off fixed phrases; the condition is
classified by a keyword lexicon into a canonical tag (life_zero, deckout, poison_ten,
commander_damage, opponents_left, concede, win_and_lose, all_lose_simultaneous, effect).
A rule whose condition doesn't match the lexicon — section headers ("There are several
ways…"), team/Emperor/tournament variants — is abstained on rather than mislabeled. These
are the rules basis for the engine's loses_game SBAs (life_zero / poison_ten already enforced).
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split


def _outcome(low: str) -> str | None:
    if "wins the game" in low:
        return "win"
    if "loses the game" in low or "lose the game" in low:
        return "lose"
    if "is a draw" in low or "be a draw" in low:
        return "draw"
    return None


def _subject(low: str) -> str:
    return "team" if "team" in low[:50] else "player"


def _condition(low: str) -> str | None:
    if "life total" in low and "0 or less" in low:
        return "life_zero"
    if "poison counters" in low and ("ten or more" in low or "10 or more" in low):
        return "poison_ten"
    if "combat damage" in low and "21 or more" in low:
        return "commander_damage"
    if "draw" in low and "library" in low and ("more" in low or "remaining" in low or "left" in low):
        return "deckout"
    if "opponents have all left" in low or ("opponents" in low and "left the game" in low):
        return "opponents_left"
    if "concede" in low:
        return "concede"
    if "both win and lose" in low:
        return "win_and_lose"
    if "remaining" in low and "lose" in low and "simultaneously" in low:
        return "all_lose_simultaneous"
    if "effect may state" in low:
        return "effect"
    return None


def extract() -> list[tuple[str, str, str, str]]:
    """(rule, outcome, subject, condition) for each §104 end-of-game condition (abstains if unclear)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "1":
            continue
        for g in s.groups:
            if g.number != "104":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    low = sr.text.strip().lower()
                    outcome = _outcome(low)
                    if not outcome:
                        continue
                    cond = _condition(low)
                    if cond is None:
                        continue
                    rows.append((sr.number, outcome, _subject(low), cond))
    return rows


_NUMWORD = {"ten": 10, "twenty-one": 21, "twenty one": 21}


def thresholds() -> list[tuple[str, str, int]]:
    """(rule, condition, n) — the numeric loss thresholds §104 states ("ten or more poison
    counters" -> 10, "21 or more combat damage" -> 21). The engine depends on these."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows = []
    for s in doc.sections:
        if s.number != "1":
            continue
        for g in s.groups:
            if g.number != "104":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    low = sr.text.strip().lower()
                    cond = _condition(low)
                    if cond == "poison_ten":
                        m = re.search(r"(ten|\d+) or more poison", low)
                        if m:
                            rows.append((sr.number, cond, _NUMWORD.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else 0)))
                    elif cond == "commander_damage":
                        m = re.search(r"(\d+) or more", low)
                        if m:
                            rows.append((sr.number, cond, int(m.group(1))))
    return rows


def build() -> tuple[str, dict]:
    rows = extract()
    thr = thresholds()
    p = Program()
    p.comment("ending.dl — §104 win/lose/draw conditions, interpreted from rules.txt.")
    p.comment("game_end(outcome, subject, condition); loss_threshold(condition, n). GENERATED.")
    p.blank()
    p.decl("game_end", [("outcome", "symbol"), ("subject", "symbol"), ("condition", "symbol")])
    p.decl("loss_threshold", [("condition", "symbol"), ("n", "number")])
    p.blank()
    for _n, outcome, subj, cond in rows:
        p.fact(f'game_end("{outcome}", "{subj}", "{cond}")')
    p.blank()
    for _n, cond, n in thr:
        p.fact(f'loss_threshold("{cond}", {n})')
    p.blank()
    p.output("game_end")
    p.output("loss_threshold")
    p.blank()
    p.comment("conformance — spot-check the conditions §104 states plainly")
    p.conformance(
        [("expect_end", [("outcome", "symbol"), ("subject", "symbol"), ("condition", "symbol")])],
        [("end", "expect_end(O, S, C)", "miss", "game_end(O, S, C)")],
    )
    for atom in ['expect_end("lose", "player", "life_zero")',
                 'expect_end("lose", "player", "deckout")',
                 'expect_end("win", "player", "opponents_left")',
                 'expect_end("draw", "player", "all_lose_simultaneous")']:
        p.fact(atom)
    return p.text(), {"count": len(rows)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/ending.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/ending.dl ({report['count']} game_end conditions)")


if __name__ == "__main__":
    main()

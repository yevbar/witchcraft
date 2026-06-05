"""Build datalog/concepts.dl — §1 Game Concepts definitional families, from rules.txt.

Three small, regular families that the rest of the engine references:

  §117.1  "A player may [cast/activate/take] [object] [any time they have priority |
           during their main phase ... priority ... empty]."
        -> player_may(action, object, timing)      timing = priority | sorcery

  §113.3  "[Spell/Activated/Triggered/Static] abilities ..."
        -> ability_category(name)                   the four ability categories

  §122.1  "A [kind] counter ... [creates a replacement/prevention effect | a triggered
           ability | modifies P/T | indicates a characteristic | grants a keyword]."
        -> counter_kind(kind, creates)              what each counter kind does

Hybrid: regex anchors each rigid frame, a small keyword lexicon classifies the counter's
effect. A counter whose effect doesn't match the lexicon (e.g. poison, an SBA reference) is
abstained on rather than mislabeled — a wrong fact is worse than no fact.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_PM = re.compile(
    r"A player may (cast|activate|take|play) ((?:an?|some|other|a) [\w\- ]+?) "
    r"(any time they have priority|whenever they have priority|during their main phase[^.]*?priority[^.]*?empty)",
    re.I)
_ART = re.compile(r"^(?:an?|some|other|a)\s+")
_KIND = re.compile(r"(?:A |An |One or more |The number of )([\w/+\-]+) counters?\b", re.I)


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def player_may() -> list[tuple[str, str, str, str]]:
    """(rule, action, object, timing) — §117.1 priority/timing permissions."""
    rows, seen = [], set()
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "117":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    for m in _PM.finditer(sr.text):
                        verb, obj, timing = m.groups()
                        obj = _ART.sub("", obj.strip()).replace(" ", "_")
                        tclass = "sorcery" if "main phase" in timing.lower() else "priority"
                        if (verb, obj, tclass) not in seen:
                            seen.add((verb, obj, tclass))
                            rows.append((sr.number, verb, obj, tclass))
    return rows


def ability_category() -> list[tuple[str, str]]:
    """(rule, name) — §113.3a-d the four ability categories."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "113":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("113.3") or sr.number == "113.3":
                        continue
                    m = re.match(r"(\w+) abilities", sr.text.strip())
                    if m:
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def _creates(text: str) -> str | None:
    low = text.lower()
    if "replacement effect" in low and "prevention effect" in low:
        return "replacement_and_prevention"
    if "replacement effect" in low:
        return "replacement"
    if "prevention effect" in low:
        return "prevention"
    if "triggered ability" in low:
        return "triggered_ability"
    if "keyword" in low and "gain that" in low:
        return "keyword_grant"
    if "indicates how much" in low:
        return "characteristic"
    if "power" in low and "toughness" in low:
        return "modifies_pt"
    return None


def counter_kind() -> list[tuple[str, str, str]]:
    """(rule, kind, creates) — §122.1 what each counter kind does (abstains if unclassifiable)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "122":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("122.1") or sr.number == "122.1":
                        continue
                    m = _KIND.search(sr.text)
                    creates = _creates(sr.text)
                    if not m or creates is None:
                        continue
                    kind = m.group(1).lower().replace("+x/+y", "p_t").replace("/", "_")
                    rows.append((sr.number, kind, creates))
    return rows


def build() -> tuple[str, dict]:
    pm, ac, ck = player_may(), ability_category(), counter_kind()
    p = Program()
    p.comment("concepts.dl — §1 Game Concepts definitional families, interpreted from rules.txt.")
    p.comment("player_may(action, object, timing); ability_category(name); counter_kind(kind, creates). GENERATED.")
    p.blank()
    p.decl("player_may", [("action", "symbol"), ("object", "symbol"), ("timing", "symbol")])
    p.decl("ability_category", [("name", "symbol")])
    p.decl("counter_kind", [("kind", "symbol"), ("creates", "symbol")])
    p.blank()
    for _n, a, o, t in pm:
        p.fact(f'player_may("{a}", "{o}", "{t}")')
    p.blank()
    for _n, name in ac:
        p.fact(f'ability_category("{name}")')
    p.blank()
    for _n, kind, creates in ck:
        p.fact(f'counter_kind("{kind}", "{creates}")')
    p.blank()
    p.output("player_may")
    p.output("ability_category")
    p.output("counter_kind")
    p.blank()
    p.comment("conformance — spot-check the definitions the rules state plainly")
    p.conformance(
        [("expect_may", [("action", "symbol"), ("object", "symbol"), ("timing", "symbol")]),
         ("expect_cat", [("name", "symbol")]),
         ("expect_ck", [("kind", "symbol"), ("creates", "symbol")])],
        [("may", "expect_may(A, O, T)", "miss", "player_may(A, O, T)"),
         ("cat", "expect_cat(N)", "miss", "ability_category(N)"),
         ("ck", "expect_ck(K, C)", "miss", "counter_kind(K, C)")],
    )
    for atom in ['expect_may("cast", "instant_spell", "priority")',
                 'expect_may("activate", "mana_ability", "priority")']:
        p.fact(atom)
    for atom in ['expect_cat("triggered")', 'expect_cat("static")']:
        p.fact(atom)
    for atom in ['expect_ck("shield", "replacement_and_prevention")',
                 'expect_ck("rad", "triggered_ability")',
                 'expect_ck("keyword", "keyword_grant")']:
        p.fact(atom)
    return p.text(), {"player_may": len(pm), "ability_category": len(ac), "counter_kind": len(ck)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/concepts.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/concepts.dl ({report['player_may']} player_may, "
          f"{report['ability_category']} ability_category, {report['counter_kind']} counter_kind)")


if __name__ == "__main__":
    main()

"""migrate_check.py — the regex-as-oracle gate for the lark migration.

For a target verb set, diff card_lark.parse_clause_lark vs card_effects.parse_clause over EVERY
regex-grounded clause in the corpus. A clause shape may be flipped to lark only once lark reproduces
the regex tuple identically (and the residual disagreements are deliberately reconciled).
"""
import sys
import collections

import ground
import card_corpus
from card_effects import parse_clause, parse_effect
from card_lark import parse_clause_lark
from transpile_card import transpile_unit, _sentences, _TRIG, _split_modifiers
import re

VERBS = set(sys.argv[1:]) or {"destroy", "exile", "tap", "untap", "sacrifice", "counter", "goad", "detain"}


def _tuple(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def clauses():
    seen = set()
    for c in card_corpus.load_cards():
        cid = ground.slug(c["name"])
        for i, u in enumerate(card_corpus.units_of(c)):
            if not transpile_unit(u, {"id": cid, "card": c, "seq": i}):
                continue
            raw = u.raw
            m = _TRIG.match(raw)
            body = m.group("body") if m else raw
            cm = re.match(r"^([^:]{1,55}):\s*(.+)$", body)
            if cm and "{" in cm.group(1):
                body = cm.group(2)
            body, _ = _split_modifiers(body)
            for s in _sentences(body):
                s = s.strip().rstrip(".")
                if s and s not in seen:
                    seen.add(s)
                    yield s


def main():
    golden = ident = diff = larkonly = regexonly = 0
    diffs, news = [], []
    for s in clauses():
        R = parse_effect(s)              # the LEAF parser (bare clause, no wrapper chain) — what lark replaces
        rg = _tuple(R)
        in_family = R is not None and R.verb in VERBS
        L = parse_clause_lark(s)
        lg = _tuple(L)
        if in_family:
            golden += 1
            if lg == rg:
                ident += 1
            elif lg is None:
                regexonly += 1
            else:
                diff += 1
                if len(diffs) < 12:
                    diffs.append((s[:50], lg, rg))
        elif lg is not None:                 # lark grounds something the regex (for this family) didn't
            if R is None:
                larkonly += 1
                if len(news) < 8:
                    news.append((s[:50], lg))
    print(f"VERBS: {sorted(VERBS)}")
    print(f"regex golden (clauses regex grounds to these verbs): {golden}")
    print(f"  lark IDENTICAL tuple : {ident} ({100*ident//max(golden,1)}%)")
    print(f"  lark DIFFERS         : {diff}   <- bugs to fix before flipping")
    print(f"  lark ABSTAINS (regex-only): {regexonly}   <- grammar gaps")
    print(f"  lark-only (regex abstained entirely): {larkonly}   <- potential net-new")
    if diffs:
        print("DIFFERENCES (lark vs regex):")
        for s, lg, rg in diffs:
            print(f"   {s}\n     lark={lg}\n     regex={rg}")
    if news:
        print("LARK-ONLY examples:")
        for s, lg in news:
            print(f"   {s}  ->  {lg}")


if __name__ == "__main__":
    main()

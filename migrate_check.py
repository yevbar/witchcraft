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
from transpile_card import (transpile_unit, _sentences, _TRIG, _split_modifiers,
                            _smart_split, _mask_q, _unmask, _leading_subject, _has_leading_subject)
import re

VERBS = set(sys.argv[1:]) or {"destroy", "exile", "tap", "untap", "sacrifice", "counter", "goad", "detain"}


def _tuple(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def _smart_parts(s):
    """The leaf clause STRINGS production's `_parse_body` would feed to `parse_clause` for a compound `s` —
    mask quoted abilities + protect 'power and toughness' + `_smart_split` + reattach the shared leading
    subject (mirrors transpile_card._parse_body L555-566). Returns [] when `s` is a single clause."""
    masked, q = _mask_q(s)
    masked = re.sub(r"power and toughness", "power\x00and\x00toughness", masked, flags=re.I)
    parts = [_unmask(p.replace("\x00", " "), q) for p in _smart_split(masked)]
    if len(parts) < 2:
        return []
    subj = _leading_subject(parts[0])
    return [f"{subj} {p}" if (j > 0 and subj and not _has_leading_subject(p)) else p
            for j, p in enumerate(parts)]


def _is_phantom(s):
    """A lark abstain on the WHOLE clause is PHANTOM (a migrate_check leaf artifact, not a real lark gap)
    when production `_smart_split`s `s` into parts lark grounds every one of — the engine still gets clean
    per-part facts. The regex's lossy whole-clause grounding that migrate_check sees never reaches production."""
    parts = _smart_parts(s)
    return bool(parts) and all(parse_clause_lark(p.strip().rstrip(".")) is not None for p in parts)


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
    golden = ident = diff = larkonly = regexonly = phantom = 0
    diffs, news, reals, phantoms = [], [], [], []
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
            elif lg is None:                 # lark abstains — split REAL grammar gaps from PHANTOM compounds
                if _is_phantom(s):           # (production _smart_splits into lark-grounded parts)
                    phantom += 1
                    if len(phantoms) < 10:
                        phantoms.append(s[:70])
                else:
                    regexonly += 1
                    if len(reals) < 16:
                        reals.append((s[:60], rg))
            else:
                diff += 1
                if len(diffs) < 12:
                    diffs.append((s[:50], lg, rg))
        elif lg is not None:                 # lark grounds something the regex (for this family) didn't
            if R is None:
                larkonly += 1
                if len(news) < 8:
                    news.append((s[:50], lg))
    abst = regexonly + phantom
    print(f"VERBS: {sorted(VERBS)}")
    print(f"regex golden (clauses regex grounds to these verbs): {golden}")
    print(f"  lark IDENTICAL tuple : {ident} ({100*ident//max(golden,1)}%)")
    print(f"  lark DIFFERS         : {diff}   <- bugs to fix before flipping")
    print(f"  lark ABSTAINS        : {abst}   ({regexonly} REAL grammar gaps + {phantom} PHANTOM compounds)")
    print(f"     PHANTOM = production _smart_splits the clause into parts lark grounds (NOT a real gap)")
    print(f"  lark-only (regex abstained entirely): {larkonly}   <- potential net-new")
    if diffs:
        print("DIFFERENCES (lark vs regex):")
        for s, lg, rg in diffs:
            print(f"   {s}\n     lark={lg}\n     regex={rg}")
    if reals:
        print("REAL ABSTAINS (actionable grammar gaps):")
        for s, rg in reals:
            print(f"   {s}  ->  regex={rg}")
    if phantoms:
        print("PHANTOM ABSTAINS (production splits these — not real gaps):")
        for s in phantoms:
            print(f"   {s}")
    if news:
        print("LARK-ONLY examples:")
        for s, lg in news:
            print(f"   {s}  ->  {lg}")


if __name__ == "__main__":
    main()

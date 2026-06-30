"""test_conditional_damage_upgrade.py — PARSER grounding of the §616.1 conditional damage UPGRADE clause:
'<src> deals N damage to <tgt>. If <cond>, it deals M damage [to <tgt>] instead.'

The base damage already grounded; the upgrade clause ('it deals M instead' / 'If <cond>, … instead') did not —
the generic 'instead' strip treated it as a no-op (which would read the upgrade as ADDITIONAL damage). It now
grounds via a structural peel in parse_clause (no new regex interpretation — the amount/target ground through
the spaCy+Lark leaf; the peel records extra='instead', the anaphoric 'that_target' when no target is named,
and a 'twice'/'half' multiplier slug). The condition rides `cond` via the existing _IF_COND/_IF_TRAIL peels.

This is PARSE-LEVEL coverage (every clause grounds to a faithful Effect); the engine/shim resolution of the
'instead' replacement is separate, downstream work. Run: MTG_NO_SPACY=1 python3 test_conditional_damage_upgrade.py
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import transpile_card
from interpreter import ground
from interpreter.card_effects import parse_clause

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def _effects(nm, corpus):
    c = corpus[nm]
    cid = ground.slug(nm)
    effs, full = [], True
    for i, u in enumerate(card_corpus.units_of(c)):
        o = transpile_card.transpile_unit(u, {"id": cid, "card": c, "seq": i})
        if o is None:
            full = False
        else:
            effs += [tuple(f.split('card_effect(')[1].rstrip(").").replace('"', '').split(", ")[3:])
                     for f in o.facts if "card_effect(" in f]
    return full, effs


def main():
    global PASS, FAIL
    # --- clause-level: the upgrade grounds with extra='instead' + the condition, anaphoric target when omitted
    cases = [
        ("If ~ was kicked, it deals 4 damage instead", 4, "that_target", "instead", "was_kicked"),
        ("It deals 5 damage instead if you control a Spacecraft", 5, "that_target", "instead", "you_control_a_spacecraft"),
        ("~ deals 5 damage instead if a creature died this turn", 5, "that_target", "instead", "a_creature_died_this_turn"),
        ("If ~ was bargained, it deals twice X damage to that permanent instead", "twice_x", "that_permanent", "instead", "was_bargained"),
    ]
    for text, amt, tgt, extra, cond in cases:
        e = parse_clause(text)
        ok = (e is not None and e.verb == "deal_damage" and str(e.amount) == str(amt)
              and e.target == tgt and e.extra == extra and e.cond == cond)
        check(f"clause grounds: {text[:48]!r}", ok)

    # --- regression: a non-damage 'instead' is still the no-op strip; a base damage is untouched
    check("non-damage 'instead' unchanged (exile it instead -> exile it)",
          parse_clause("exile it instead").extra == "-")
    check("base damage unchanged (no spurious 'instead')",
          parse_clause("~ deals 2 damage to any target").extra == "-")

    # --- card-level full-ingest (the 4 targets + the Flaming Gambit non-regression)
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    for nm in ["Burst Lightning", "Brimstone Volley", "Invasive Maneuvers", "Stonesplitter Bolt"]:
        full, effs = _effects(nm, corpus)
        has_instead = any("instead" in e for e in effs)
        check(f"{nm}: full-ingest with an 'instead' upgrade effect", full and has_instead)
    full, _ = _effects("Flaming Gambit", corpus)
    check("Flaming Gambit: still full-ingest (redirect 'deal that damage … instead' not mis-gated)", full)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)

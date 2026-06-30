"""test_translate_effects.py — §608/§701.5/§615/§111 the CONSTANT-form effect verbs counter /
prevent_damage(fog) / create whose generic-tail emission (spell_effect / trigger_effect) is now DERIVED
IN DATALOG (translate.dl) from the card parse facts instead of by the python bridge's _resolved_effect.

Equivalence bar: over the WHOLE corpus, for every card compute the OLD bridge tuples these 3 verbs would
have produced (call the bridge generic-tail logic directly via _resolved_effect, gated exactly as the old
branches were) and compare against the DATALOG-derived spell_effect / trigger_effect rows (feed the card's
parse facts into a tiny state, read driver.run(st, [...])). The two MUST be byte-for-byte EQUAL per card.

Run: python3 test_translate_effects.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import souffle_eval
import bridge_to_engine as bridge
from driver import RULES

# the three verbs / engine effect-names this slice owns. We filter both the old-bridge and the
# datalog-derived rows to these so the comparison ignores rows other branches still own.
_OWNED_EFF = {"counter", "fog", "create_token"}


def _old_spell_rows(f) -> set:
    """The spell_effect rows the OLD bridge generic tail (r = _resolved_effect(verb,amt,tgt,extra)) would
    have emitted for the 3 owned verbs, keyed by the spell instance id. Mirrors the spell branch exactly:
    _resolved_effect handles the create numeric+spec guard and the prevent_damage fog gate internally.
    §700.2 modal MODE abilities are excluded — their effects resolve via the mode-gated spell_effect_mode,
    NOT the flat spell_effect (the bridge no longer feeds modes as card_ability/card_effect to the engine)."""
    rows = set()
    modes = set(f.get("modes", []))
    for aid, ab in f.get("abilities", {}).items():
        if aid in modes or ab.get("kind") != "spell":
            continue
        for (_seq, verb, amt, tgt, extra, _cond) in ab.get("effects", []):
            if verb not in ("counter", "prevent_damage", "create"):
                continue
            r = bridge._resolved_effect(verb, amt, tgt, extra)
            if r is not None and r[0] in _OWNED_EFF:
                rows.add(("x", r[0], str(r[1]), r[2]))    # str(amount): souffle CSV reads amounts as strings
    return rows


def _old_trigger_rows(f) -> set:
    """The trigger_effect rows the OLD bridge generic tail would have emitted for the 3 owned verbs, keyed
    by the instance ability id f'{tid}_{aid}'. The triggered branch only reaches the generic tail when the
    trigger maps to an engine event (_EVENT) — mirror that gate; cond is ignored by the generic tail."""
    rows = set()
    modes = set(f.get("modes", []))
    for aid, ab in f.get("abilities", {}).items():
        if aid in modes or ab.get("kind") != "triggered":
            continue
        if bridge._EVENT.get(ab.get("trigger")) is None:     # unmapped trigger -> branch 'continue's, no tail
            continue
        a = f"x_{aid}"
        for (_seq, verb, amt, tgt, extra, _cond) in ab.get("effects", []):
            if verb not in ("counter", "prevent_damage", "create"):
                continue
            r = bridge._resolved_effect(verb, amt, tgt, extra)
            if r is not None and r[0] in _OWNED_EFF:
                rows.add((a, r[0], str(r[1]), r[2]))      # str(amount): souffle CSV reads amounts as strings
    return rows


# the REAL engine rules + a .output for trigger_effect (which the committed engine doesn't surface, but the
# rule is in RULES). Evaluated through souffle_eval — the COMPILED native binary first (the souffle
# interpreter's compiled mode is broken on some installs), returning None for the handful of cards whose
# 'put_counter X' clause aborts EITHER backend in an unrelated §122 to_number, so the caller skips them.
_PROG = RULES + "\n.output trigger_effect\n"


def _datalog_rows(state: dict):
    """Feed the card's parse facts to the engine, read the DATALOG-derived spell_effect / trigger_effect rows
    for this instance ('x'), filtered to the 3 owned engine effect-names. None if the backend aborts (skip)."""
    out = souffle_eval.eval_state(_PROG, state)
    if out is None:
        return None
    sp = {r for r in out.get("spell_effect", set()) if r[0] == "x" and r[1] in _OWNED_EFF}
    tr = {r for r in out.get("trigger_effect", set()) if r[0].startswith("x_") and r[1] in _OWNED_EFF}
    return sp, tr


def run() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    checks = 0
    mismatches = 0
    skipped = 0
    n_counter = n_fog = n_create = 0
    n_tcounter = n_tfog = n_tcreate = 0

    for name in corpus:
        try:
            f_state, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        # the bridge's per-card interpreted facts (abilities/effects/modes) — the dict sim.load_db()['<slug>']
        facts = db.get(bridge.ground.slug(name), {})

        old_sp = _old_spell_rows(facts)
        old_tr = _old_trigger_rows(facts)

        # only pay for an engine eval when the parse facts even contain an owned verb (most cards don't).
        verbs = {v for (_c, _a, _i, v, *_r) in f_state.get("card_effect", set())}
        if verbs & {"counter", "prevent_damage", "create"}:
            st = {k: f_state[k] for k in ("instance_of", "card_ability", "card_effect", "ability_trigger") if k in f_state}
            st["is_player"] = {("alice",), ("bob",)}
            res = _datalog_rows(st)
            if res is None:                                   # backend aborted on this card (§122 to_number) -> skip
                skipped += 1
                continue
            dl_sp, dl_tr = res
        else:
            dl_sp, dl_tr = set(), set()

        checks += 1
        if old_sp != dl_sp or old_tr != dl_tr:
            mismatches += 1
            if mismatches <= 20:
                print(f"  MISMATCH {name!r}")
                print(f"    spell old-bridge:   {sorted(old_sp)}")
                print(f"    spell datalog:      {sorted(dl_sp)}")
                print(f"    trigger old-bridge: {sorted(old_tr)}")
                print(f"    trigger datalog:    {sorted(dl_tr)}")
            continue

        for r in dl_sp:
            n_counter += r[1] == "counter"
            n_fog += r[1] == "fog"
            n_create += r[1] == "create_token"
        for r in dl_tr:
            n_tcounter += r[1] == "counter"
            n_tfog += r[1] == "fog"
            n_tcreate += r[1] == "create_token"

    print(f"\nspell   : counter={n_counter}  fog={n_fog}  create_token={n_create}")
    print(f"trigger : counter={n_tcounter}  fog={n_tfog}  create_token={n_tcreate}")
    if skipped:
        print(f"({skipped} cards skipped — souffle backend aborted on an unrelated §122 to_number)")
    print(f"{checks - mismatches}/{checks} checks passed  ({mismatches} mismatches)")
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

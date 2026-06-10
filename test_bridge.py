"""test_bridge.py — the cards.dl -> rules-engine bridge is faithful, and real cards play via the datalog.

Asserts (a) card_facts translates real oracle facts into the engine's input vocabulary correctly,
(b) unsupported clauses abstain rather than mistranslate, and (c) a real card's interpreted triggered
ability actually fires when the datalog rules engine resolves a game. Run: python3 test_bridge.py
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import card_corpus
import sim
import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _derive(state: dict, outputs: list[str]) -> dict:
    """Read the engine-DERIVED printed_* (and other outputs) back for `state` via the engine. printed_* are
    a .output surface, so the engine emits the per-instance rows it derived from the card-level card_* facts."""
    return driver.run({k: set(v) for k, v in state.items()}, outputs)


_IDENTITY_RELS = ["printed_type", "printed_subtype", "printed_color",
                  "printed_power", "printed_toughness", "printed_keyword"]
_NUMERIC_RELS = {"printed_power", "printed_toughness"}


def _old_bridge_printed(c: dict, f: dict, tid: str) -> dict:
    """The PRE-migration bridge's per-instance printed_* (verbatim old logic, off the MTGJSON corpus dict)."""
    o = {k: set() for k in _IDENTITY_RELS}
    for t in c.get("types") or []:
        o["printed_type"].add((tid, t.lower()))
    for st in c.get("subtypes") or []:
        o["printed_subtype"].add((tid, st.lower()))
    for ci in c.get("colorIdentity") or []:
        if ci in bridge._COLOR_NAME:
            o["printed_color"].add((tid, bridge._COLOR_NAME[ci]))
    p, t = c.get("power"), c.get("toughness")
    if str(p or "").lstrip("-").isdigit():
        o["printed_power"].add((tid, int(p)))
    if str(t or "").lstrip("-").isdigit():
        o["printed_toughness"].add((tid, int(t)))
    for kw in f.get("keywords", set()):
        if kw in bridge._ENGINE_KEYWORDS:
            o["printed_keyword"].add((tid, kw))
    return o


def _engine_join(bf: dict, tid: str) -> dict:
    """The translate.dl rule semantics (instance_of join over card_*, +engine_keyword guard for keywords)
    applied to the NEW bridge's card_* facts — the printed_* the engine derives, computed in Python."""
    e = {k: set() for k in _IDENTITY_RELS}
    for (_s, t) in bf.get("card_type", set()):
        e["printed_type"].add((tid, t))
    for (_s, x) in bf.get("card_subtype", set()):
        e["printed_subtype"].add((tid, x))
    for (_s, x) in bf.get("card_color", set()):
        e["printed_color"].add((tid, x))
    for (_s, x) in bf.get("card_power", set()):
        e["printed_power"].add((tid, x))
    for (_s, x) in bf.get("card_toughness", set()):
        e["printed_toughness"].add((tid, x))
    for (_s, kw) in bf.get("card_keyword", set()):
        if kw in bridge._ENGINE_KEYWORDS:                    # the engine_keyword(Kw) guard
            e["printed_keyword"].add((tid, kw))
    return e


def _check_corpus_identity_equivalence(db: dict, corpus: dict) -> None:
    import ground
    tid = "x"
    mism = total = 0
    for name in corpus:
        c = corpus[name]
        f = db.get(ground.slug(name), {})
        bf, _ = bridge.card_facts(name, "alice", tid, db, corpus)
        old = _old_bridge_printed(c, f, tid)
        new = _engine_join(bf, tid)
        for r in _IDENTITY_RELS:
            total += len(new[r])
            mism += old[r] != new[r]
    check(f"engine-derived printed_* == old bridge for ALL {len(corpus)} cards "
          f"({total} derived rows, 0 mismatches)", mism == 0)

    # confirm the REAL souffle engine derives byte-identical rows end-to-end for a deterministic sample.
    import random
    eng_mism = 0
    for name in random.Random(0).sample(list(corpus), 50):
        c, f = corpus[name], db.get(ground.slug(name), {})
        bf, _ = bridge.card_facts(name, "alice", tid, db, corpus)
        st = {k: set(v) for k, v in bf.items()}
        st["on_battlefield"] = {(tid,)}
        st.setdefault("is_player", set()).update({("alice",), ("bob",)})
        out = driver.run(st, _IDENTITY_RELS)
        new = {r: {(o, int(v)) if r in _NUMERIC_RELS else (o, v) for (o, v) in out.get(r, set())}
               for r in _IDENTITY_RELS}
        old = _old_bridge_printed(c, f, tid)
        eng_mism += any(old[r] != new[r] for r in _IDENTITY_RELS)
    check("the real souffle engine derives byte-identical printed_* for a 50-card sample (0 mismatches)",
          eng_mism == 0)


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name):
        return bridge.card_facts(name, "alice", "x", db, corpus)

    # vanilla creature: ONE WORLD — the bridge feeds the CARD-LEVEL identity (card_type/card_power keyed by
    # slug + instance_of), and the engine DERIVES the per-instance printed_* via translate.dl. No abilities,
    # nothing abstained.
    f, dropped = facts("Grizzly Bears")
    check("vanilla creature -> card-level identity fed (card_power 2, card_type creature) + instance_of, no drops",
          ("grizzly_bears", 2) in f.get("card_power", set())
          and ("grizzly_bears", "creature") in f.get("card_type", set())
          and ("x", "grizzly_bears") in f.get("instance_of", set())
          and "printed_power" not in f and "printed_type" not in f
          and not dropped and "has_trigger" not in f)
    # the engine DERIVES the per-instance printed_* from those card-level facts (translate.dl).
    st = {k: set(v) for k, v in f.items()}
    eng = _derive(st, ["printed_power", "printed_toughness", "printed_type"])
    check("the engine DERIVES printed_power/toughness/type from the card-level identity facts",
          ("x", "2") in eng["printed_power"] and ("x", "2") in eng["printed_toughness"]
          and ("x", "creature") in eng["printed_type"])

    # keyword creature: the bridge feeds card_keyword (all keywords); the engine derives printed_keyword for
    # the keywords it models (engine_keyword guard) — flying + vigilance.
    f, _ = facts("Serra Angel")
    check("keyword creature -> card_keyword(flying, vigilance) fed",
          {("serra_angel", "flying"), ("serra_angel", "vigilance")} <= f.get("card_keyword", set())
          and "printed_keyword" not in f)
    st = {k: set(v) for k, v in f.items()}
    eng = _derive(st, ["printed_keyword"])
    check("the engine DERIVES printed_keyword(flying, vigilance) from card_keyword",
          {("x", "flying"), ("x", "vigilance")} <= eng["printed_keyword"])

    # CORPUS EQUIVALENCE — for EVERY card, the printed_* the engine derives from the NEW bridge's
    # card_*+instance_of facts (rule: printed_X(I,V):-instance_of(I,C),card_X(C,V), +engine_keyword for kw)
    # EXACTLY equals the OLD bridge's per-instance printed_* emission (read off the corpus dict). The rule is
    # a pure relational join, so the derivation is computed in Python for ALL cards (fast); a small sample is
    # then run through the REAL souffle engine to confirm it derives byte-identical rows end-to-end.
    _check_corpus_identity_equivalence(db, corpus)

    # a supported death trigger: ONE WORLD — the bridge feeds ONLY the card PARSE facts (no has_trigger,
    # no trigger_effect); the engine DERIVES both has_trigger(dies_self) and the player-scoped effect
    # (lose 2 life) in DATALOG (translate.dl) from those facts.
    f, dropped = facts("Tattered Mummy")
    check("the bridge no longer emits has_trigger (datalog derives it)", "has_trigger" not in f)
    check("the bridge feeds the card PARSE facts (one world): card_ability(triggered) + ability_trigger(dies)",
          ("tattered_mummy", "a0", "triggered") in f.get("card_ability", set())
          and ("tattered_mummy", "a0", "dies") in f.get("ability_trigger", set()))
    check("the bridge feeds the card PARSE facts (one world): card_effect(lose_life, 2, each_opponent)",
          any(verb == "lose_life" and amt == "2" and tgt == "each_opponent"
              for (_c, _a, _s, verb, amt, tgt, _e, _co) in f.get("card_effect", set())))
    check("the engine DERIVES the effect from those facts (no python trigger_effect emitted)",
          not f.get("trigger_effect") and not dropped)
    # the engine DERIVES has_trigger end-to-end: feed the parse facts + kill the source (0 toughness SBA) and
    # confirm the dies_self trigger fires (fires derives only from has_trigger) and the effect resolves.
    st = {k: set(v) for k, v in f.items()}                    # facts() built this instance with tid "x"
    st["on_battlefield"] = {("x",)}
    st["printed_toughness"] = {("x", 0)}
    st.setdefault("is_player", set()).update({("alice",), ("bob",)})
    st.setdefault("active_player", set()).add(("alice",))
    st.setdefault("current_step", set()).add(("upkeep",))
    eng = driver.run(st, ["fires", "pending"])
    check("the engine DERIVES has_trigger(dies_self) from the parse facts (fires(x_a0, x))",
          ("x_a0", "x") in eng["fires"])
    check("...and resolves the derived effect -> pending(lose_life, 2, each_opponent)",
          ("x_a0", "lose_life", "2", "each_opponent", "x", "alice") in eng["pending"])

    # an UNsupported effect abstains (no mistranslation) — Gravedigger's ETB return_to_hand
    f, dropped = facts("Gravedigger")
    check("unsupported effect abstains, not mistranslated",
          ("effect", "return_to_hand") in dropped and not f.get("trigger_effect"))

    # end-to-end: a real card's interpreted trigger fires when the datalog resolves a game
    buf = io.StringIO()
    with redirect_stdout(buf):
        bridge.demo_game()
    log = buf.getvalue()
    check("real card's death trigger fires through the datalog engine",
          "tattered_mummy" in log and "loses 2" in log and "dies -> graveyard" in log)
    check("bridge authors no rules: bob's life drops below 18 only via the trigger",
          "bob loses 2 -> 18" in log)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

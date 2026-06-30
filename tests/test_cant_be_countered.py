"""test_cant_be_countered.py — §701.5f a spell with a 'self can't be countered' static can't be countered.

Three-layer wiring + the driver counter-resolution guard:
  L1  sim.load_db captures cant(card,"self","be_countered") into db[card]["cant"].
  L2  bridge.card_facts surfaces it as the driver-only `uncounterable` flag on the spell INSTANCE.
  L3  driver._cant_be_countered reads the flag; _run_spell_effects' `counter` arm refuses to counter
      such a victim (it stays on the stack), while a normal spell is still countered (no regression).
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import card_corpus
import sim
import driver
import bridge_to_engine as bridge


def _run(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        raise SystemExit(f"FAILED: {name}")


def main():
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # L1 — load_db captured the self restriction
    em = db.get("emrakul_the_aeons_torn", {})
    _run("L1 load_db captures cant(self, be_countered)", ("self", "be_countered") in em.get("cant", set()))

    # L2 — card_facts surfaces `uncounterable` per instance (and not for a vanilla creature)
    f_em, _ = bridge.card_facts("Emrakul, the Aeons Torn", "alice", "emk1", db, corpus)
    f_gb, _ = bridge.card_facts("Grizzly Bears", "alice", "gb1", db, corpus)
    _run("L2 uncounterable flagged on the uncounterable spell", ("emk1",) in f_em.get("uncounterable", set()))
    _run("L2 vanilla creature NOT flagged", not f_gb.get("uncounterable"))

    # L3 — the helper
    _run("L3 _cant_be_countered True for flagged", driver._cant_be_countered({"uncounterable": {("emk1",)}}, "emk1"))
    _run("L3 _cant_be_countered False otherwise", not driver._cant_be_countered({"uncounterable": {("emk1",)}}, "x"))

    # --- the driver counter-resolution guard (isolate the stack logic; stub the downstream resolvers) ---
    orig = (driver._spell_effects, driver._pending_both, driver._apply_effects, driver._to_graveyard)
    driver._spell_effects = lambda state, spell: [(spell, "counter", "0", "target_spell")]
    driver._pending_both = lambda state: (set(), set())
    driver._apply_effects = lambda state, *a: None
    driver._to_graveyard = lambda state, obj: state.setdefault("graveyard", set()).add((obj,))
    try:
        # (a) uncounterable victim SURVIVES: counterspell 'cs' on top, Emrakul 'emk1' below, flagged uncounterable
        st = {"on_stack": {("cs", 1), ("emk1", 0)}, "uncounterable": {("emk1",)}, "_stack_info": {}}
        driver._run_spell_effects(st, "cs", "alice")
        _run("uncounterable victim stays on the stack", ("emk1", 0) in st["on_stack"])
        _run("uncounterable victim NOT put in graveyard", ("emk1",) not in st.get("graveyard", set()))

        # (b) NO REGRESSION: a normal (counterable) victim is still countered (leaves the stack)
        st2 = {"on_stack": {("cs", 1), ("bear", 0)}, "uncounterable": set(), "_stack_info": {}}
        driver._run_spell_effects(st2, "cs", "alice")
        _run("counterable victim leaves the stack (countered)", ("bear", 0) not in st2["on_stack"])
        _run("counterable victim put in graveyard", ("bear",) in st2.get("graveyard", set()))
    finally:
        driver._spell_effects, driver._pending_both, driver._apply_effects, driver._to_graveyard = orig

    print("\nALL OK")


if __name__ == "__main__":
    main()

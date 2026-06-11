"""test_storm.py — §608 the cast counter + §707.10 spell copying + §702.40 STORM.

The two shared infrastructure pieces (cast counter, spell copy) and storm, their first consumer:
  • _note_cast / state['_cast_count'] — spells cast THIS TURN (by any player), reset each turn boundary.
  • _copy_spell — N copies on the stack that RE-DERIVE their effects from instance_of (no per-effect
    plumbing) and cease to exist on resolution (no graveyard, no phantom state left behind).
  • _storm — a storm spell is copied once per spell cast before it this turn; verified on the three
    canonical payoffs: Grapeshot (damage), Tendrils of Agony (drain), Empty the Warrens (tokens).

Run: python3 test_storm.py
"""

from __future__ import annotations

import card_corpus
import driver
import bridge_to_engine as B
import sim

CORPUS = {c["name"]: c for c in card_corpus.load_cards()}
DB = sim.load_db()
CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _life(st, p):
    return next(m for (q, m) in st["life"] if q == p)


def _state(cast_count=0):
    return {
        "is_player": {("alice",), ("bob",)}, "on_battlefield": set(), "in_hand": set(),
        "printed_control": set(), "instance_of": set(), "spell_type": set(), "on_stack": set(),
        "_stack_info": {}, "graveyard": set(), "life": {("alice", 20), ("bob", 20)},
        "current_step": {("precombat_main",)}, "active_player": {("alice",)},
        "mana_pool": {("alice", "black", 12), ("alice", "red", 12)}, "mana_available": {("alice", 12)},
        "_cast_count": cast_count,
    }


def _install(st, name, tid, zone="hand"):
    facts, _ = B.card_facts(name, "alice", tid, DB, CORPUS)
    for rel, rows in facts.items():
        st.setdefault(rel, set()).update(rows)
    for t in CORPUS[name].get("types") or []:
        st["spell_type"].add((tid, t.lower()))
    st.setdefault("mana_cost", set()).add((tid, 1))
    if zone == "hand":
        st["in_hand"].add(("alice", tid))


def _copy_infra():
    # _copy_spell: a copy re-derives its effects; resolving it leaves NO trace (ceases to exist).
    st = _state()
    _install(st, "Grapeshot", "gs0")
    cps = driver._copy_spell(st, "gs0", "alice", 2)
    check("copy: two copies are created on the stack", len(cps) == 2 and all((c,) in st["_is_copy"] for c in cps))
    check("copy: a copy re-derives its damage effect via instance_of",
          all(any(r[0] == c for r in driver.run(st, ["spell_damage"])["spell_damage"]) for c in cps))
    # resolve the whole stack; copies should deal damage then vanish leaving no phantom state.
    driver._resolve_stack(st, "alice", ["alice", "bob"])
    check("copy: resolved copies deal damage (bob 20 -> 18)", _life(st, "bob") == 18)
    check("copy: copies cease to exist (none left in _is_copy)", not st.get("_is_copy"))
    check("copy: no phantom copy ids linger in instance_of/spell_type",
          not [r for r in st["instance_of"] if "copy" in r[0]] and not [r for r in st["spell_type"] if "copy" in r[0]])
    check("copy: a copy never lands in the graveyard", not [g for (g,) in st["graveyard"] if "copy" in g])


def _storm_payoffs():
    # Grapeshot with 2 prior spells -> 1 + 2 copies = 3 damage.
    st = _state(cast_count=2)
    _install(st, "Grapeshot", "gs0")
    driver._cast_spell(st, "alice", "gs0", ["alice", "bob"])
    check("storm: Grapeshot (2 prior) deals 3 (bob 17)", _life(st, "bob") == 17)
    check("storm: the cast counter advanced to 3", st["_cast_count"] == 3)

    # Tendrils of Agony with 3 prior -> drain 2 x4 = bob -8, alice +8.
    st = _state(cast_count=3)
    _install(st, "Tendrils of Agony", "ta0")
    driver._cast_spell(st, "alice", "ta0", ["alice", "bob"])
    check("storm: Tendrils (3 prior) drains 8 (bob 12)", _life(st, "bob") == 12)
    check("storm: Tendrils (3 prior) gains 8 (alice 28)", _life(st, "alice") == 28)

    # Empty the Warrens with 2 prior -> 2 goblins x3 = 6 tokens.
    st = _state(cast_count=2)
    _install(st, "Empty the Warrens", "ew0")
    driver._cast_spell(st, "alice", "ew0", ["alice", "bob"])
    check("storm: Empty the Warrens (2 prior) makes 6 goblins", len(st["on_battlefield"]) == 6)

    # storm with ZERO prior spells -> just the original, no copies.
    st = _state(cast_count=0)
    _install(st, "Grapeshot", "gs0")
    driver._cast_spell(st, "alice", "gs0", ["alice", "bob"])
    check("storm: no prior spells -> no copies (bob 19)", _life(st, "bob") == 19)

    # a NON-storm spell with prior spells does NOT copy.
    st = _state(cast_count=5)
    _install(st, "Lightning Bolt", "lb0")
    driver._cast_spell(st, "alice", "lb0", ["alice", "bob"])
    check("non-storm: Lightning Bolt with 5 prior still deals only 3", _life(st, "bob") == 17)


def _cast_counter_increments():
    # casting several spells in one turn advances the counter so a later storm spell sees them all.
    st = _state(cast_count=0)
    _install(st, "Lightning Bolt", "lb0")
    _install(st, "Lightning Bolt", "lb1")
    _install(st, "Grapeshot", "gs0")
    driver._cast_spell(st, "alice", "lb0", ["alice", "bob"])
    driver._cast_spell(st, "alice", "lb1", ["alice", "bob"])
    check("counter: two real casts bring _cast_count to 2", st["_cast_count"] == 2)
    bob = _life(st, "bob")
    driver._cast_spell(st, "alice", "gs0", ["alice", "bob"])
    check("counter: Grapeshot after 2 casts storms for 3 (1 + 2 copies)", _life(st, "bob") == bob - 3)


def _counter_resets_each_turn():
    # the storm count is per-turn: passing the turn (via the env) zeroes _cast_count so next turn's storm
    # spells don't count this turn's casts.
    import contextlib
    import io

    import env
    st = B.make_deck_state({"alice": ["Mountain"] * 40, "bob": ["Mountain"] * 40}, seed=1, hand=3, life=20)
    st["active_player"] = {("alice",)}; st["current_step"] = {("end_of_turn",)}; st["_cast_count"] = 4
    s = env.start(st)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(50):
            if next(iter(s["active_player"]))[0] == "bob":
                break
            s = env.step(s, ("pass",))
    check("counter: passing the turn resets _cast_count to 0", s.get("_cast_count") == 0)


def _storm_kill_is_searchable():
    # END-TO-END through the env/search: a turn-1 storm kill (20 life) is DISCOVERABLE by win_search.
    # 9 Lotus Petals (each a spell -> +1 storm count, sacrificed for mana) then Tendrils of Agony: storm 9
    # -> 10 drains of 2 = 20. The search must develop mana from the board (no pre-seeded pool).
    import env
    import win_search
    deck = ["Lotus Petal"] * 9 + ["Tendrils of Agony"] + ["Swamp"] * 30
    st = B.make_deck_state({"alice": deck, "bob": ["Mountain"] * 30}, seed=1, hand=0, life=20)

    def fids(slug):
        return [t for (t, n) in sorted(st["instance_of"]) if n == slug and ("alice", t) in st["in_library"]]

    petals = fids("lotus_petal")[:9]
    tend = fids("tendrils_of_agony")[0]
    for t in petals + [tend]:
        st["in_library"].discard(("alice", t)); st["in_hand"].add(("alice", t))
        if t in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(t)
    st["active_player"] = {("alice",)}; st["current_step"] = {("untap",)}
    st.pop("mana_pool", None); st.pop("mana_available", None)
    driver.clear_cache()
    path, _nodes = win_search.find_win(st, me="alice", max_turns=1, node_budget=200000)
    casts = [a for a in (path or []) if a[0] == "cast"]
    check("storm kill: win_search finds a turn-1 storm kill (20 life)", path is not None)
    check("storm kill: the line builds storm with 9 Lotus Petals + casts Tendrils",
          sum(1 for a in casts if a[2] in petals) == 9 and any(a[2] == tend for a in casts))


def run():
    _copy_infra()
    _storm_payoffs()
    _cast_counter_increments()
    _counter_resets_each_turn()
    _storm_kill_is_searchable()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

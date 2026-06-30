"""test_combat_restrictions.py — structural #5 (combat restrictions/requirements): 'can't be blocked'.

The engine reads a state input relation cant_be_blocked(creature); illegal_block(B,A) :- blocks(B,A),
cant_be_blocked(A) then forbids ANY blocker from pairing with such an attacker. This is honored uniformly:
  * env._legal_block_pairs / _block_options (the referee's block decision surface) — already probe illegal_block;
  * the driver's combat (blocked / deals) — already keys on illegal_block;
so a single engine rule wires the restriction into BOTH the agent's legal-move enumeration and damage resolution.

Tests:
  1. an unblockable attacker is NEVER paired with a blocker in env's legal block options (PERFECT info);
  2. a CONTROL attacker (no restriction) IS blockable (the restriction is targeted, not global);
  3. the engine derives illegal_block for the unblockable attacker and NOT for the control one;
  4. combat damage: an unblockable attacker hits the player even though a blocker was 'assigned';
  5. IMPERFECT info — combat is public, so observe(defender) sees the same restriction and the block
     options computed over the OBSERVED state still never pair a blocker with the unblockable attacker;
  6. END-TO-END resolution — a spell/ability whose 'cant_be_blocked' effect resolves writes the state row
     (the driver applier path), and end-of-turn cleanup clears it.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib
import io

import env
import driver
import observe

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _block_state(unblockable: set[str] | None = None) -> dict:
    """bob (defending) faces alice's two attackers 'sneak' (2/2) and 'normal' (3/3); bob has a blocker
    'wall' (0/4). It's the declare_blockers step (attackers already declared). `unblockable` = the set of
    alice's attackers carrying the 'can't be blocked' restriction (the engine input cant_be_blocked)."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_blockers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("sneak",), ("normal",), ("wall",)},
        "printed_type": {("sneak", "creature"), ("normal", "creature"), ("wall", "creature")},
        "printed_power": {("sneak", 2), ("normal", 3), ("wall", 0)},
        "printed_toughness": {("sneak", 2), ("normal", 3), ("wall", 4)},
        "printed_control": {("alice", "sneak"), ("alice", "normal"), ("bob", "wall")},
        "attacks": {("sneak", "bob"), ("normal", "bob")},
        "blocks": set(), "tapped": set(), "_sick": set(), "counter": set(),
    }
    if unblockable:
        st["cant_be_blocked"] = {(c,) for c in unblockable}
    return st


def _enforced_in_block_options() -> None:
    st = _block_state(unblockable={"sneak"})
    pairs = env._legal_block_pairs(st, "bob")
    check("no legal block pair targets the unblockable attacker 'sneak'",
          all(a != "sneak" for (_b, a) in pairs))
    check("the blockable attacker 'normal' IS offered as a legal block",
          ("wall", "normal") in pairs)

    # the env's full block-option enumeration (what an agent actually chooses from)
    actions = env.legal_actions(st)
    block_sets = [a[1] for a in actions if a[0] == "block"]
    flat = {pr for fs in block_sets for pr in fs}
    check("env.legal_actions block options never include a (blocker, 'sneak') pair",
          all(a != "sneak" for (_b, a) in flat))
    check("env still offers a real block of 'normal'",
          any(("wall", "normal") in fs for fs in block_sets))


def _control_is_blockable() -> None:
    st = _block_state(unblockable=None)            # nobody is unblockable
    pairs = env._legal_block_pairs(st, "bob")
    check("control: with no restriction, 'sneak' IS blockable", ("wall", "sneak") in pairs)
    check("control: 'normal' IS blockable too", ("wall", "normal") in pairs)


def _engine_derives_illegal_block() -> None:
    st = _block_state(unblockable={"sneak"})
    st["blocks"] = {("wall", "sneak"), ("wall", "normal")}   # probe BOTH as if declared
    ib = driver.run(st, ["illegal_block"])["illegal_block"]
    check("engine derives illegal_block(wall, sneak) for the unblockable attacker", ("wall", "sneak") in ib)
    check("engine does NOT make blocking the control attacker illegal", ("wall", "normal") not in ib)


def _combat_damage_respects_it() -> None:
    """A blocker 'assigned' to an unblockable attacker is an ILLEGAL block (illegal_block above): the
    attacker is treated as UNBLOCKED and hits the player — verified via the engine's player_damage output
    (what the driver persists to life). With only 'sneak' attacking and (illegally) 'blocked', bob still
    takes its 2 damage; without the restriction the same block would absorb it (0 to bob)."""
    st = _block_state(unblockable={"sneak"})
    st["current_step"] = {("combat_damage",)}
    st["attacks"] = {("sneak", "bob")}             # isolate the unblockable attacker
    st["blocks"] = {("wall", "sneak")}             # bob (illegally) tries to block sneak with wall
    pd = {p: int(n) for (p, n) in driver.run(st, ["player_damage"])["player_damage"]}
    check("the unblockable attacker's damage reaches the player (bob takes 2) despite the assigned blocker",
          pd.get("bob") == 2)

    # control: the SAME block, WITHOUT the restriction, absorbs the damage (bob takes 0).
    ctl = dict(st); ctl.pop("cant_be_blocked", None)
    pd_ctl = {p: int(n) for (p, n) in driver.run(ctl, ["player_damage"])["player_damage"]}
    check("control: a legal block of 'sneak' absorbs the damage (bob takes 0)", pd_ctl.get("bob", 0) == 0)


def _imperfect_info_public() -> None:
    """Combat is public: observe(bob) keeps the cant_be_blocked rows (they reference public battlefield
    creatures), so the block options computed over the OBSERVED state hold the same restriction."""
    st = _block_state(unblockable={"sneak"})
    # add a hidden private zone to prove observe redacts it but keeps the public combat restriction.
    st["in_hand"] = {("alice", "secret")}
    obs = observe.observe(st, "bob")
    check("observe(bob) hides alice's hand", not any(p == "alice" for (p, _c) in obs.get("in_hand", set())))
    check("observe(bob) preserves the public cant_be_blocked restriction",
          ("sneak",) in obs.get("cant_be_blocked", set()))
    pairs = env._legal_block_pairs(obs, "bob")
    check("on the OBSERVED state, no block pair targets the unblockable attacker",
          all(a != "sneak" for (_b, a) in pairs) and ("wall", "normal") in pairs)


def _resolution_and_cleanup() -> None:
    """The driver applier path: resolving a 'this creature can't be blocked' self-effect writes the row;
    a targeted one writes the chosen creature's row; cleanup clears the restriction at end of turn."""
    # self-effect path (effect_handlers.permanents._apply_cant_be_blocked via _apply_effects)
    st = _block_state(unblockable=None)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("trigA", "cant_be_blocked", 0, "-", "sneak", "alice")})
    check("a resolved self 'cant_be_blocked' effect writes cant_be_blocked(source)",
          ("sneak",) in st.get("cant_be_blocked", set()))
    pairs = env._legal_block_pairs(st, "bob")
    check("after the effect resolves, 'sneak' is unblockable in env", all(a != "sneak" for (_b, a) in pairs))

    # targeted path (_apply_target_verb)
    st2 = _block_state(unblockable=None)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_target_verb(st2, "spellX", "spell", "cant_be_blocked", "-", "normal",
                                  "alice", set(), {})
    check("a resolved 'target creature cant_be_blocked' writes the chosen creature's row",
          ("normal",) in st2.get("cant_be_blocked", set()))

    # end-of-turn cleanup clears the (this-turn) restriction
    st3 = _block_state(unblockable={"sneak"})
    with contextlib.redirect_stdout(io.StringIO()):
        driver._end_of_turn(st3)
    check("cant_be_blocked is cleared by end-of-turn cleanup", not st3.get("cant_be_blocked"))


def _bridge_encodes_self_scope() -> None:
    """The bridge ENCODE entry (effect_handlers.permanents._encode_cant_be_blocked): a SELF-scope
    'this creature can't be blocked' must reach the applier — without an encoder the bridge dropped it
    even though the applier handles it. 'self'/'it' route to the source; target/that_creature abstain."""
    import effect_handlers
    effect_handlers.load()
    enc = effect_handlers.ENCODE.get("cant_be_blocked")
    check("cant_be_blocked has a bridge ENCODE entry", enc is not None)
    check("encoder routes 'self' to the source", enc and enc("cant_be_blocked", 0, "self", "-") == ("cant_be_blocked", 0, "self"))
    check("encoder routes 'it' to the source", enc and enc("cant_be_blocked", 0, "it", "-") == ("cant_be_blocked", 0, "it"))
    check("encoder ABSTAINS on target_creature (driver target-pick)", enc and enc("cant_be_blocked", 0, "target_creature", "-") is None)
    check("encoder ABSTAINS on anaphoric that_creature", enc and enc("cant_be_blocked", 0, "that_creature", "-") is None)
    # end-to-end through the bridge: a self-scope card no longer drops the clause
    from interpreter import card_corpus
    import sim, bridge_to_engine as bridge
    db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
    if "Aetherling" in corpus:
        _f, dropped = bridge.card_facts("Aetherling", "p", "t0", db, corpus)
        check("Aetherling: the self cant_be_blocked clause is no longer dropped",
              all(d != "cant_be_blocked" for _k, d in dropped))


def run() -> None:
    _enforced_in_block_options()
    _control_is_blockable()
    _engine_derives_illegal_block()
    _combat_damage_respects_it()
    _imperfect_info_public()
    _resolution_and_cleanup()
    _bridge_encodes_self_scope()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

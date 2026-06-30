"""test_combat_restrict2.py — COMBAT-RESTRICTION BREADTH (structural #5, restriction-side):
cant_attack ('can't attack'), lure ('all creatures able to block this creature do so'), detain
('can't attack or block until the detainer's next turn').

Companion to test_combat_requirements.py (the must_* side) and test_combat_restrictions.py (cant_be_blocked).
The engine surfaces three more combat-state relations the REFEREE (env) consumes when enumerating the legal
declare-attackers / declare-blockers options:
  cant_attack(c) — driver-only EDB (set by the cant_attack applier) UNIONED with the engine's defender +
                   static-aura derivations; consumed by may_attack(C) :- ... !cant_attack(C) -> env drops it.
  lure(c)        — driver-only EDB + engine-derived; unioned into must_be_blocked (the >=1-blocker floor),
                   AND env enforces the STRONG 'all able blockers' set-level requirement.
  detained(c)    — driver-only EDB; unioned into cant_attack (attack half) + read by env._legal_block_pairs
                   for the block half. (Expiry + abilities-can't-activate abstain — see the handler caveat.)

Tests:
  1. a cant_attack creature NEVER appears as an attacker option (perfect info);
  2. CONTROL: without the restriction it IS an attacker option;
  3. the engine derives cant_attack from a STATIC AURA ('enchanted creature can't attack');
  4. a self-cant_attack applier (resolved 'this creature can't attack') writes the EDB + drops it from env;
  5. lure: every offered block set forces ALL able blockers onto the lured attacker;
  6. lure unions into must_be_blocked (the >=1-blocker floor still holds);
  7. detain: the detained creature is excluded from BOTH attack options and block pairs;
  8. a self-detain applier writes detained(source) and the engine/env exclude it from attack + block;
  9. IMPERFECT info — combat is public, so observe(seat) keeps the restrictions and the options computed
     over the OBSERVED state enforce them identically.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

import env
import driver
import observe

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---- attack-side: cant_attack / detain -----------------------------------------------------------------

def _attack_state(cant: set[str] | None = None, detained: set[str] | None = None) -> dict:
    """alice's declare_attackers step: she controls 'r' (2/2) and 'free' (3/3), both able to attack.
    `cant` = the driver-only cant_attack EDB rows; `detained` = the driver-only detained EDB rows."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_attackers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("r",), ("free",)},
        "printed_type": {("r", "creature"), ("free", "creature")},
        "printed_power": {("r", 2), ("free", 3)},
        "printed_toughness": {("r", 2), ("free", 3)},
        "printed_control": {("alice", "r"), ("alice", "free")},
        "attacks": set(), "blocks": set(), "_sick": set(), "counter": set(), "tapped": set(),
    }
    if cant:
        st["cant_attack"] = {(c,) for c in cant}
    if detained:
        st["detained"] = {(c,) for c in detained}
    return st


def _cant_attack_excluded() -> None:
    st = _attack_state(cant={"r"})
    ma = driver.run(st, ["may_attack"])["may_attack"]
    check("cant_attack creature 'r' is excluded from may_attack", ("r",) not in ma)
    opts = env._attack_options(st, "alice")
    check("'r' NEVER appears in any attack option", opts and all("r" not in s for s in opts))
    check("'free' is still an attacker option", any("free" in s for s in opts))
    acts = [a[1] for a in env.legal_actions(st) if a[0] == "attack"]
    check("env.legal_actions never offers an attack set containing the cant_attack creature",
          acts and all("r" not in s for s in acts))


def _cant_attack_control() -> None:
    st = _attack_state(cant=None)
    opts = env._attack_options(st, "alice")
    check("control: without cant_attack, 'r' IS offered as an attacker", any("r" in s for s in opts))


def _cant_attack_static_aura() -> None:
    """A static aura ('enchanted creature can't attack', the attack half of Pacifism) excludes the enchanted
    creature — derived in the engine from card_ability(static)+attached_to, no applier."""
    st = _attack_state()
    st["on_battlefield"].add(("aura",))
    st["printed_type"].add(("aura", "enchantment"))
    st["printed_control"].add(("alice", "aura"))
    st["instance_of"] = {("aura", "pacifism_like")}
    st["card_ability"] = {("pacifism_like", "x", "static")}
    st["card_effect"] = {("pacifism_like", "x", 0, "cant_attack", "-", "enchanted_creature", "-", "-")}
    st["attached_to"] = {("aura", "r")}
    ca = driver.run(st, ["cant_attack"])["cant_attack"]
    check("a static aura derives cant_attack(enchanted) in the engine", ("r",) in ca)
    opts = env._attack_options(st, "alice")
    check("env excludes the aura-restricted creature from every attack option",
          opts and all("r" not in s for s in opts))


def _self_cant_attack_applier() -> None:
    """A resolved 'this creature can't attack' self-effect writes cant_attack(source); env drops it."""
    st = _attack_state()
    _quiet(driver._apply_effects, st, {("trg", "cant_attack", 0, "self", "r", "alice")})
    check("a resolved self cant_attack writes cant_attack(source)", ("r",) in st.get("cant_attack", set()))
    check("the self-restricted creature is then excluded from env attack options",
          all("r" not in s for s in env._attack_options(st, "alice")))


# ---- lure ----------------------------------------------------------------------------------------------

def _block_state(lure: set[str] | None = None, two_blockers: bool = False) -> dict:
    """bob (defending) faces alice's attackers 'big' (4/4) and 'small' (2/2). bob has blocker 'w1' (0/4),
    plus 'w2' (0/3) when two_blockers. declare_blockers step. `lure` marks an attacker all able blockers
    must block (§509)."""
    bf = {("big",), ("small",), ("w1",)}
    ptype = {("big", "creature"), ("small", "creature"), ("w1", "creature")}
    ppow = {("big", 4), ("small", 2), ("w1", 0)}
    ptou = {("big", 4), ("small", 2), ("w1", 4)}
    pctl = {("alice", "big"), ("alice", "small"), ("bob", "w1")}
    if two_blockers:
        bf.add(("w2",)); ptype.add(("w2", "creature")); ppow.add(("w2", 0))
        ptou.add(("w2", 3)); pctl.add(("bob", "w2"))
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_blockers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": bf, "printed_type": ptype, "printed_power": ppow, "printed_toughness": ptou,
        "printed_control": pctl,
        "attacks": {("big", "bob"), ("small", "bob")},
        "blocks": set(), "tapped": set(), "_sick": set(), "counter": set(),
    }
    if lure:
        st["lure"] = {(c,) for c in lure}
    return st


def _lure_forces_all_blockers() -> None:
    st = _block_state(lure={"big"}, two_blockers=True)
    opts = [a[1] for a in env.legal_actions(st) if a[0] == "block"]
    # every offered set must have BOTH able blockers blocking 'big' (the lured attacker).
    def all_block_big(s):
        return all((b, "big") in s for b in ("w1", "w2"))
    check("every offered block set forces ALL able blockers onto the lured attacker 'big'",
          opts and all(all_block_big(s) for s in opts))
    check("the no-block set is NOT offered when a lured attacker can be blocked",
          frozenset() not in opts)
    # control: no lure -> the no-block set is legal again
    ctl = _block_state(two_blockers=True)
    check("control: with no lure, no-block IS a legal option",
          frozenset() in [a[1] for a in env.legal_actions(ctl) if a[0] == "block"])


def _lure_unions_must_be_blocked() -> None:
    st = _block_state(lure={"big"})
    mbb = driver.run(st, ["must_be_blocked"])["must_be_blocked"]
    check("lure(c) unions into must_be_blocked(c) (the >=1-blocker floor)", ("big",) in mbb)


# ---- detain --------------------------------------------------------------------------------------------

def _detain_excludes_attack_and_block() -> None:
    # attack side: a detained creature can't attack
    st = _attack_state(detained={"r"})
    check("detained unions into cant_attack -> excluded from may_attack",
          ("r",) not in driver.run(st, ["may_attack"])["may_attack"])
    check("a detained creature never appears in an attack option",
          all("r" not in s for s in env._attack_options(st, "alice")))
    # block side: a detained creature can't block
    bst = _block_state()
    bst["detained"] = {("w1",)}
    pairs = env._legal_block_pairs(bst, "bob")
    check("a detained creature is excluded from legal block pairs (can't block)",
          all(b != "w1" for (b, _a) in pairs))
    # control: without detain, w1 CAN block
    ctl = _block_state()
    check("control: without detain, the same creature CAN block",
          any(b == "w1" for (b, _a) in env._legal_block_pairs(ctl, "bob")))


def _self_detain_applier() -> None:
    st = _attack_state()
    _quiet(driver._apply_effects, st, {("trg", "detain", 0, "self", "r", "alice")})
    check("a resolved self detain writes detained(source)", ("r",) in st.get("detained", set()))
    check("the self-detained creature is excluded from env attack options",
          all("r" not in s for s in env._attack_options(st, "alice")))


# ---- imperfect information -----------------------------------------------------------------------------

def _imperfect_info_public() -> None:
    """The three restrictions live on public battlefield state, so observe(seat) keeps them and the option
    enumeration over the OBSERVED state enforces them identically."""
    # attack side (cant_attack), observed by the active player alice
    st = _attack_state(cant={"r"})
    st["in_hand"] = {("bob", "secret")}
    obs = observe.observe(st, "alice")
    check("observe(alice) hides bob's hand", not any(p == "bob" for (p, _c) in obs.get("in_hand", set())))
    check("observe(alice) preserves the public cant_attack restriction", ("r",) in obs.get("cant_attack", set()))
    check("on the OBSERVED state, the cant_attack creature is still excluded from attacks",
          all("r" not in s for s in env._attack_options(obs, "alice")))

    # block side (lure), observed by the defender bob
    bst = _block_state(lure={"big"}, two_blockers=True)
    bst["in_hand"] = {("alice", "secret")}
    bobs = observe.observe(bst, "bob")
    check("observe(bob) preserves the public lure restriction", ("big",) in bobs.get("lure", set()))
    bopts = [a[1] for a in env.legal_actions(bobs) if a[0] == "block"]
    check("on the OBSERVED state, every block set forces all able blockers onto the lured attacker",
          bopts and all(all((b, "big") in s for b in ("w1", "w2")) for s in bopts))


def run() -> None:
    _cant_attack_excluded()
    _cant_attack_control()
    _cant_attack_static_aura()
    _self_cant_attack_applier()
    _lure_forces_all_blockers()
    _lure_unions_must_be_blocked()
    _detain_excludes_attack_and_block()
    _self_detain_applier()
    _imperfect_info_public()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

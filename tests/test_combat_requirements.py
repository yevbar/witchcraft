"""test_combat_requirements.py — structural #5 (combat REQUIREMENTS, must-side): goad / must_attack /
must_be_blocked / must_block / remove_from_combat.

Mirrors the cant_be_blocked precedent (test_combat_restrictions.py) but for the MUST side. The engine
surfaces four combat-state relations the REFEREE (env) consumes when enumerating the legal declare-
attackers / declare-blockers options:
  goaded(c) / must_attack(c)   — driver-only EDB (set by the goad/must_attack applier) UNIONED with the
                                  engine's static-aura derivation; goaded is unioned into must_attack.
  must_be_blocked(c) / must_block(c) — static-aura / self-static derivation + driver-only EDB.
Unlike illegal_block (a per-block predicate) these are SET-level requirements ('the chosen attack/block
set must include this creature if it's able'), so enforcement lives in env._attack_options / _block_options.

Tests:
  1. a must-attacker (goaded / 'attacks each combat') is in EVERY legal attack option when ABLE;
  2. CONTROL: the same creature TAPPED (unable) is NOT forced — '§508.1a if able';
  3. a goaded creature is forced (goaded -> must_attack in the engine);
  4. a static aura ('enchanted creature must attack') forces the enchanted creature (engine-derived);
  5. must_be_blocked: an attacker that must be blocked is never left unblocked while a blocker exists;
  6. must_block: a creature that must block is never left idle when a legal block exists;
  7. remove_from_combat clears a creature from attacks/blocks (the applier one-shot);
  8. IMPERFECT info — combat is public, so observe(seat) sees the same requirements and the options
     computed over the OBSERVED state still enforce them.
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

from mtg.engine import env
from mtg import driver
from mtg.engine import observe

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---- attack-side: must_attack / goad -------------------------------------------------------------------

def _attack_state(goaded: set[str] | None = None, tapped: set[str] | None = None) -> dict:
    """alice's declare_attackers step: she controls 'goader' (2/2) and 'free' (3/3), both able to attack.
    `goaded` = the driver-only goaded EDB rows; `tapped` taps a creature (an UNABLE attacker)."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_attackers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("goader",), ("free",)},
        "printed_type": {("goader", "creature"), ("free", "creature")},
        "printed_power": {("goader", 2), ("free", 3)},
        "printed_toughness": {("goader", 2), ("free", 3)},
        "printed_control": {("alice", "goader"), ("alice", "free")},
        "attacks": set(), "blocks": set(), "_sick": set(), "counter": set(),
        "tapped": {(c,) for c in (tapped or set())},
    }
    if goaded:
        st["goaded"] = {(c,) for c in goaded}
    return st


def _must_attacker_forced() -> None:
    st = _attack_state(goaded={"goader"})
    opts = env._attack_options(st, "alice")
    check("a goaded creature is in EVERY legal attack option",
          opts and all("goader" in s for s in opts))
    check("the non-forced creature 'free' is still optional (some option omits it)",
          any("free" not in s for s in opts))
    check("the goader-only set is a legal option", frozenset({"goader"}) in opts)
    # via legal_actions (what an agent actually sees)
    acts = env.legal_actions(st)
    asets = [a[1] for a in acts if a[0] == "attack"]
    check("env.legal_actions never offers an attack set omitting the goaded creature",
          asets and all("goader" in s for s in asets))


def _tapped_not_forced() -> None:
    """§508.1a 'if able' — a tapped (unable) must-attacker is NOT forced; the empty attack is legal again."""
    st = _attack_state(goaded={"goader"}, tapped={"goader"})
    opts = env._attack_options(st, "alice")
    check("a TAPPED goaded creature is NOT forced (unable -> the empty attack is legal again)",
          frozenset() in opts)
    check("a tapped goaded creature is not a forced FLOOR (some option omits it)",
          any("goader" not in s for s in opts))


def _goaded_implies_must_attack() -> None:
    st = _attack_state(goaded={"goader"})
    ma = driver.run(st, ["must_attack"])["must_attack"]
    check("the engine unions goaded into must_attack", ("goader",) in ma)
    ctl = _attack_state(goaded=None)
    check("control: no goad -> no must_attack", not driver.run(ctl, ["must_attack"])["must_attack"])


def _static_aura_must_attack() -> None:
    """A static aura ('enchanted creature attacks each combat if able', e.g. Uncontrollable Anger) forces
    the enchanted creature — derived in the engine from card_ability(static)+attached_to, no applier."""
    st = _attack_state()
    st["on_battlefield"].add(("aura",))
    st["printed_type"].add(("aura", "enchantment"))
    st["printed_control"].add(("alice", "aura"))
    st["instance_of"] = {("aura", "uncontrollable_anger")}
    st["card_ability"] = {("uncontrollable_anger", "a2b", "static")}
    st["card_effect"] = {("uncontrollable_anger", "a2b", 0, "must_attack", "-", "enchanted_creature", "-", "-")}
    st["attached_to"] = {("aura", "goader")}
    ma = driver.run(st, ["must_attack"])["must_attack"]
    check("a static aura forces the enchanted creature to attack (engine-derived)", ("goader",) in ma)
    opts = env._attack_options(st, "alice")
    check("env forces the aura-enchanted creature into every attack option",
          opts and all("goader" in s for s in opts))


# ---- block-side: must_be_blocked / must_block ----------------------------------------------------------

def _block_state(must_be_blocked: set[str] | None = None, must_block: set[str] | None = None) -> dict:
    """bob (defending) faces alice's attackers 'lure' (2/2) and 'other' (3/3); bob has a blocker 'wall'
    (0/4). declare_blockers step. `must_be_blocked` marks an attacker that must be blocked if able;
    `must_block` marks a bob creature that must block if able."""
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_blockers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("lure",), ("other",), ("wall",)},
        "printed_type": {("lure", "creature"), ("other", "creature"), ("wall", "creature")},
        "printed_power": {("lure", 2), ("other", 3), ("wall", 0)},
        "printed_toughness": {("lure", 2), ("other", 3), ("wall", 4)},
        "printed_control": {("alice", "lure"), ("alice", "other"), ("bob", "wall")},
        "attacks": {("lure", "bob"), ("other", "bob")},
        "blocks": set(), "tapped": set(), "_sick": set(), "counter": set(),
    }
    if must_be_blocked:
        st["must_be_blocked"] = {(c,) for c in must_be_blocked}
    if must_block:
        st["must_block"] = {(c,) for c in must_block}
    return st


def _must_be_blocked_enforced() -> None:
    st = _block_state(must_be_blocked={"lure"})
    opts = [a[1] for a in env.legal_actions(st) if a[0] == "block"]
    check("every offered block set blocks the must-be-blocked attacker (a blocker is available)",
          opts and all(any(a == "lure" for (_b, a) in s) for s in opts))
    check("the empty (no-block) set is NOT offered when a forced block is possible",
          frozenset() not in opts)
    # control: no requirement -> the no-block set is legal again
    ctl = _block_state()
    check("control: with no must_be_blocked, no-block IS a legal option",
          frozenset() in [a[1] for a in env.legal_actions(ctl) if a[0] == "block"])


def _must_block_enforced() -> None:
    st = _block_state(must_block={"wall"})
    opts = [a[1] for a in env.legal_actions(st) if a[0] == "block"]
    check("every offered block set uses the must-block creature 'wall' (a legal block exists)",
          opts and all(any(b == "wall" for (b, _a) in s) for s in opts))
    check("the no-block set is NOT offered when 'wall' must block",
          frozenset() not in opts)


# ---- remove_from_combat -------------------------------------------------------------------------------

def _remove_from_combat_clears() -> None:
    st = {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("combat_damage",)},
        "on_battlefield": {("striker",), ("guard",)},
        "printed_type": {("striker", "creature"), ("guard", "creature")},
        "attacks": {("striker", "bob")}, "blocks": {("guard", "striker")},
        "tapped": set(), "counter": set(),
    }
    _quiet(driver._apply_effects, st, {("eff", "remove_from_combat", 0, "self", "striker", "alice")})
    check("remove_from_combat drops the creature from attacks", ("striker", "bob") not in st.get("attacks", set()))
    check("remove_from_combat also drops blocks mentioning it", ("guard", "striker") not in st.get("blocks", set()))
    # a creature NOT named is untouched
    st2 = dict(st); st2["attacks"] = {("striker", "bob"), ("rogue", "bob")}; st2["blocks"] = set()
    _quiet(driver._apply_effects, st2, {("eff", "remove_from_combat", 0, "self", "striker", "alice")})
    check("remove_from_combat leaves OTHER attackers in combat", ("rogue", "bob") in st2.get("attacks", set()))


def _self_goad_applier() -> None:
    """The trigger/spell self path: a resolved 'this creature is goaded' self-effect writes goaded(source),
    which the engine turns into a must_attack — drivable end-to-end through _apply_effects."""
    st = _attack_state()
    _quiet(driver._apply_effects, st, {("trg", "goad", 0, "self", "goader", "alice")})
    check("a resolved self-goad writes goaded(source)", ("goader",) in st.get("goaded", set()))
    check("the self-goaded creature is then forced to attack via env",
          all("goader" in s for s in env._attack_options(st, "alice")))


# ---- imperfect information ----------------------------------------------------------------------------

def _imperfect_info_public() -> None:
    """Combat requirements live on public battlefield state, so observe(seat) keeps them and the option
    enumeration over the OBSERVED state enforces them identically."""
    # attack side, observed by the active player alice
    st = _attack_state(goaded={"goader"})
    st["in_hand"] = {("bob", "secret")}                       # a hidden private zone observe must redact
    obs = observe.observe(st, "alice")
    check("observe(alice) hides bob's hand", not any(p == "bob" for (p, _c) in obs.get("in_hand", set())))
    check("observe(alice) preserves the public goaded restriction", ("goader",) in obs.get("goaded", set()))
    check("on the OBSERVED state, the goaded creature is still forced to attack",
          all("goader" in s for s in env._attack_options(obs, "alice")))

    # block side, observed by the defender bob
    bst = _block_state(must_be_blocked={"lure"})
    bst["in_hand"] = {("alice", "secret")}
    bobs = observe.observe(bst, "bob")
    check("observe(bob) preserves the public must_be_blocked restriction",
          ("lure",) in bobs.get("must_be_blocked", set()))
    bopts = [a[1] for a in env.legal_actions(bobs) if a[0] == "block"]
    check("on the OBSERVED state, every block set blocks the must-be-blocked attacker",
          bopts and all(any(a == "lure" for (_b, a) in s) for s in bopts))


def run() -> None:
    _must_attacker_forced()
    _tapped_not_forced()
    _goaded_implies_must_attack()
    _static_aura_must_attack()
    _must_be_blocked_enforced()
    _must_block_enforced()
    _remove_from_combat_clears()
    _self_goad_applier()
    _imperfect_info_public()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

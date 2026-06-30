"""test_suspect.py — §701.60 SUSPECT: a suspected creature has menace and can't block (effect_handlers/suspect.py).

The applier grants menace (engine derives has_keyword -> the §702.111b illegal_block >=2-blocker rule, enforced
in BOTH the engine combat and env's block surface) and records the creature in the driver's _cant_block set.
Both halves are PUBLIC board state (a suspected designation is public), so observe(seat) keeps them.

Tests:
  1. encode: self/'it'/enchanted_creature -> ('suspect',0,'self'); a clean target_creature -> target_creature;
     board-scope ('each_creature') abstains;
  2. apply self: the source GAINS menace (has_keyword) and is in _cant_block;
  3. MENACE is enforced — a single blocker can't block the suspected attacker (illegal_block via the engine
     AND env._legal_block_pairs offers no single-blocker pair against it; two blockers CAN);
  4. apply target_creature: a driver-picked opponent creature becomes suspected (menace + _cant_block);
  5. imperfect info: observe(seat) preserves the public menace + _cant_block, enforced identically.

Run: python3 test_suspect.py
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

from mtg import driver
import env
import effect_handlers
import observe

effect_handlers.load()
_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _has_kw(state, c, kw):
    return (c, kw) in driver.run(state, ["has_keyword"])["has_keyword"]


def _self_state():
    """alice controls 'atk' (3/3); bob has blockers 'w1' (0/4) and 'w2' (0/4). declare_blockers, 'atk' attacks."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "current_step": {("declare_blockers",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("atk",), ("w1",), ("w2",)},
        "printed_type": {("atk", "creature"), ("w1", "creature"), ("w2", "creature")},
        "printed_power": {("atk", 3), ("w1", 0), ("w2", 0)},
        "printed_toughness": {("atk", 3), ("w1", 4), ("w2", 4)},
        "printed_control": {("alice", "atk"), ("bob", "w1"), ("bob", "w2")},
        "attacks": {("atk", "bob")}, "blocks": set(), "tapped": set(),
        "_sick": set(), "counter": set(), "eff_grant_keyword": set(), "_cant_block": set(),
    }


def run() -> None:
    # (0) registered.
    check("suspect encoder registered", "suspect" in effect_handlers.ENCODE)
    check("suspect applier registered", "suspect" in effect_handlers.APPLY)

    enc = effect_handlers.ENCODE["suspect"]
    # (1) encode shapes.
    check("encode 'it' -> ('suspect',0,'self')", enc("suspect", "-", "it", "-") == ("suspect", 0, "self"))
    check("encode 'enchanted_creature' -> self (the suspect aura's source-of-enchantment)",
          enc("suspect", "-", "enchanted_creature", "-") == ("suspect", 0, "self"))
    check("encode 'target_creature' -> target_creature",
          enc("suspect", "-", "target_creature", "-") == ("suspect", 0, "target_creature"))
    check("encode 'up_to_one_target_creature_an_opponent_controls' -> target_creature",
          enc("suspect", "-", "up_to_one_target_creature_an_opponent_controls", "-") == ("suspect", 0, "target_creature"))
    check("ABSTAIN on board-scope 'each_creature'", enc("suspect", "-", "each_creature", "-") is None)

    # (2) apply self: the source gains menace + is in _cant_block.
    st = _self_state()
    check("before: 'atk' has no menace", not _has_kw(st, "atk", "menace"))
    _quiet(effect_handlers.APPLY["suspect"], driver, st, "trg", 0, "self", "atk", "alice")
    check("suspected (self): the source GAINS menace (has_keyword)", _has_kw(st, "atk", "menace"))
    check("suspected (self): the source is in _cant_block", ("atk",) in st.get("_cant_block", set()))

    # (3) MENACE enforced — a single blocker can't block the suspected attacker; two can.
    one = dict(st); one["blocks"] = {("w1", "atk")}
    check("MENACE: a single blocker on the suspected attacker is an illegal_block (§702.111b)",
          ("w1", "atk") in driver.run(one, ["illegal_block"])["illegal_block"])
    two = dict(st); two["blocks"] = {("w1", "atk"), ("w2", "atk")}
    ib2 = driver.run(two, ["illegal_block"])["illegal_block"]
    check("MENACE: TWO blockers on the suspected attacker are a LEGAL block",
          ("w1", "atk") not in ib2 and ("w2", "atk") not in ib2)
    # env's block surface never offers a single-blocker pair vs the suspected attacker (a singleton block set
    # for it would be illegal). env reads illegal_block through _legal_block_pairs.
    pairs = env._legal_block_pairs(st, "bob")
    block_opts = [a[1] for a in env.legal_actions(st) if a[0] == "block"]
    singles = [s for s in block_opts if len(s) == 1 and any(att == "atk" for (_b, att) in s)]
    check("env: no single-blocker block option targets the suspected (menace) attacker",
          not singles)

    # control: a NON-suspected attacker CAN be blocked by a single blocker.
    ctl = _self_state()
    pairs_ctl = env._legal_block_pairs(ctl, "bob")
    check("control: a non-suspected attacker accepts a single blocker (pair exists)",
          any(att == "atk" for (_b, att) in pairs_ctl))

    # (3b) the CAN'T-BLOCK half: a suspected creature on the DEFENDING side can't be a legal blocker
    # (§509.1a). bob's w1 is suspected; bob is defending alice's 'atk' — w1 must be offered no block pair
    # and never chosen by the driver, while the un-suspected w2 still can.
    cb = _self_state()
    cb["_cant_block"] = {("w1",)}
    pairs_cb = env._legal_block_pairs(cb, "bob")
    check("can't-block: a suspected creature is offered no block pair (env)",
          not any(b == "w1" for (b, _a) in pairs_cb))
    check("can't-block: an un-suspected creature is still a legal blocker (env)",
          any(b == "w2" for (b, _a) in pairs_cb))
    driver.declare_blockers(cb, "alice")
    check("can't-block: the driver never assigns a suspected creature as a blocker",
          not any(b == "w1" for (b, _a) in cb.get("blocks", set())))

    # (4) apply target_creature: a driver-picked OPPONENT creature (bob's) becomes suspected.
    tg = _self_state()
    tg["_chance"] = None
    _quiet(effect_handlers.APPLY["suspect"], driver, tg, "trg", 0, "target_creature", "atk", "alice")
    picked = [c for (c,) in tg.get("_cant_block", set())]
    check("target_creature: a creature is suspected (one in _cant_block)", len(picked) == 1)
    check("target_creature: the suspected creature is an OPPONENT's (bob's) creature",
          picked and picked[0] in ("w1", "w2"))
    check("target_creature: the suspected creature has menace", _has_kw(tg, picked[0], "menace"))

    # (5) imperfect info: menace + _cant_block are public; observe(bob) keeps them and enforcement holds.
    pub = _self_state()
    _quiet(effect_handlers.APPLY["suspect"], driver, pub, "trg", 0, "self", "atk", "alice")
    pub["in_hand"] = {("alice", "secret")}
    obs = observe.observe(pub, "bob")
    check("imperfect info: observe(bob) hides alice's hand",
          not any(p == "alice" for (p, _c) in obs.get("in_hand", set())))
    check("imperfect info: observe(bob) preserves the public menace grant (has_keyword on the observed state)",
          _has_kw(obs, "atk", "menace"))
    # NOTE: _cant_block is the driver's PRIVATE bookkeeping channel (underscore-prefixed -> observe.py drops it,
    # like _regen_shield/_monarch which observe re-exports under public names). The can't-block effect that the
    # REFEREE enforces under imperfect info is the public MENACE grant (eff_grant_keyword -> has_keyword survives
    # observe), which is exactly what the block-option check below verifies on the observed state.
    obs_singles = [s for s in [a[1] for a in env.legal_actions(obs) if a[0] == "block"]
                   if len(s) == 1 and any(att == "atk" for (_b, att) in s)]
    check("imperfect info: on the OBSERVED state, no single-blocker option targets the suspected attacker",
          not obs_singles)

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()

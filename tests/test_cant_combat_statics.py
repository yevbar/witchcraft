"""test_cant_combat_statics.py — §509 SELF static combat restrictions enforced through the engine.

The printed statics '~ can't be blocked' (75 cards) and '~ can't block' (102 cards) parse to
cant(card,"self","be_blocked"/"block") but were INERT — the engine ignored them. They now feed the
engine's `cant` input (bridge.card_facts) and derive illegal_block (build_engine.py), so combat respects
them. Full vertical via real cards (load_db -> card_facts -> cant -> illegal_block -> player_damage):
  * Phantom Warrior 'can't be blocked' -> any block against it is illegal -> it hits the player.
  * Gravecrawler 'can't block'        -> any block it declares is illegal -> the attacker hits the player.
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from mtg import sim
from mtg import driver
from mtg import bridge_to_engine as bridge

_DB = sim.load_db()
_CORPUS = {c["name"]: c for c in card_corpus.load_cards()}


def _facts_for(name, ctrl, tid):
    f, _ = bridge.card_facts(name, ctrl, tid, _DB, _CORPUS)
    return f


def _merge(*factdicts):
    st: dict = {}
    for fd in factdicts:
        for rel, rows in fd.items():
            st.setdefault(rel, set()).update(rows)
    return st


def _run(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        raise SystemExit(f"FAILED: {name}")


def _combat(attacker_facts, att_id, blocker_facts, blk_id):
    st = _merge(attacker_facts, blocker_facts)
    st["is_player"] = {("alice",), ("bob",)}
    st["active_player"] = {("alice",)}
    st["life"] = {("alice", 20), ("bob", 20)}
    st["current_step"] = {("combat_damage",)}
    st["on_battlefield"] = {(att_id,), (blk_id,)}
    st["attacks"] = {(att_id, "bob")}
    st["blocks"] = {(blk_id, att_id)}
    st.setdefault("tapped", set())
    st.setdefault("counter", set())
    return st


def main():
    # sanity: load_db captured the combat statics
    _run("L1 Phantom Warrior cant(self, be_blocked)",
         ("self", "be_blocked") in _DB.get("phantom_warrior", {}).get("cant", set()))
    _run("L1 Gravecrawler cant(self, block)",
         ("self", "block") in _DB.get("gravecrawler", {}).get("cant", set()))

    # card_facts surfaces the `cant` engine input
    pw = _facts_for("Phantom Warrior", "alice", "pw1")
    _run("L2 card_facts emits cant(phantom_warrior, self, be_blocked)",
         ("phantom_warrior", "self", "be_blocked") in pw.get("cant", set()))

    # --- (A) 'can't be blocked': Phantom Warrior (alice) attacks bob, a Grizzly Bears (bob) blocks ---
    bears = _facts_for("Grizzly Bears", "bob", "gb1")
    st = _combat(pw, "pw1", bears, "gb1")
    ib = driver.run(st, ["illegal_block"]).get("illegal_block", set())
    pd = {p: int(n) for (p, n) in driver.run(st, ["player_damage"]).get("player_damage", set())}
    _run("A can't-be-blocked: the block is illegal", ("gb1", "pw1") in ib)
    _run("A can't-be-blocked: attacker's damage reaches bob (2)", pd.get("bob") == 2)

    # control: a vanilla attacker IS absorbed by the same blocker (bob takes 0)
    bear_att = _facts_for("Grizzly Bears", "alice", "ga1")
    cst = _combat(bear_att, "ga1", bears, "gb1")
    pdc = {p: int(n) for (p, n) in driver.run(cst, ["player_damage"]).get("player_damage", set())}
    _run("A control: a normal attacker is blocked (bob takes 0)", pdc.get("bob", 0) == 0)

    # --- (B) 'can't block': Gravecrawler (bob) tries to block a Grizzly Bears attacker (alice) ---
    gc = _facts_for("Gravecrawler", "bob", "gc1")
    st2 = _combat(bear_att, "ga1", gc, "gc1")
    ib2 = driver.run(st2, ["illegal_block"]).get("illegal_block", set())
    pd2 = {p: int(n) for (p, n) in driver.run(st2, ["player_damage"]).get("player_damage", set())}
    _run("B can't-block: Gravecrawler's block is illegal", ("gc1", "ga1") in ib2)
    _run("B can't-block: attacker's damage reaches bob (2)", pd2.get("bob") == 2)

    print("\nALL OK")


if __name__ == "__main__":
    main()

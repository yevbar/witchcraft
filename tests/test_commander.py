"""test_commander.py — the COMMANDER format (§903), 1v1 / Duel Commander.

Proves the structural prerequisites for cEDH, with the strict ENGINE/SHIM split this codebase requires:

  ENGINE (datalog)  owns the RULES: starting_life("commander", 40) (starting.dl), cast_permission /
                    resolves_to over the spell's TYPE, can_afford over the mana model.
  SHIM (python)     owns the STATE the engine doesn't carry across casts: the command zone, the
                    per-commander recast count (the §903.8 {2}-per-prior-cast tax), and the §903.9 /
                    §704.5 "to the command zone instead" replacement choice.

Covered:
  SETUP        — both seats start at 40 life (READ from the rules), the commander in a command zone, a
                 99-card singleton library, a 7-card opening hand drawn from the 99.
  LEGALITY     — §903.4 color identity: every card's identity ⊆ the commander's (the decks we build).
  CAST         — the commander is cast FROM the command zone (sorcery speed), reusing the normal cast ->
                 resolve -> ETB path; it enters the battlefield.
  TAX          — §903.8: each recast from the command zone costs {2} more (folded into the cost so the
                 untouched mana model pays it); the recast count escalates the surcharge.
  REPLACEMENT  — §903.9 / §704.5: a commander that would die/be exiled MAY return to the command zone
                 instead (a _choose decision), and that increments the tax for next time.
  END-TO-END   — a full 1v1 Commander game runs through the referee (env, random policy) to a decisive
                 result, with the commander cast from the command zone (and recast with tax) along the way.

Needs datalog/cards.dl + the corpus (the bridge feeds real card facts). Run: python3 test_commander.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import contextlib
import io

import bridge_to_engine as bridge
import card_corpus
import driver
import env
import game

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---- setup (§903 / §103.4) ---------------------------------------------------------------------------

def _setup() -> None:
    s = game.new_commander_game(seed=1)
    check("both seats start at 40 life (commander, read from the rules)",
          sorted(s["life"]) == [("alice", 40), ("bob", 40)])
    cz = s["command_zone"]
    check("the command zone holds one commander per player", len(cz) == 2
          and {p for (p, _c) in cz} == {"alice", "bob"})
    check("each commander is recorded as owned (_commander_owner)",
          {(p, c) for (p, c) in s["_commander_owner"]} == {(p, c) for (p, c) in cz})
    for p in ("alice", "bob"):
        hand = [c for (pp, c) in s["in_hand"] if pp == p]
        lib = [c for (pp, c) in s["in_library"] if pp == p]
        check(f"{p} draws a 7-card opening hand from the 99", len(hand) == 7)
        check(f"{p}'s singleton library is the rest of the 99 (92 after the draw)", len(lib) == 92)
        check(f"{p}'s commander is NOT in the library or hand (it's in the command zone)",
              not any(driver._is_commander(s, c) for c in hand + lib))
    check("commanders start with a zero recast tax (§903.8)",
          all(driver._commander_tax(s, c) == 0 for (_p, c) in cz))


# ---- color identity (§903.4) -------------------------------------------------------------------------

def _color_identity() -> None:
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    for p in ("alice", "bob"):
        cmds = bridge._COMMANDER_COMMANDERS[p]
        deck = bridge._COMMANDER_DECKS[p]
        ok, offenders = bridge.color_identity_legal(cmds, deck, corpus)
        check(f"{p}'s 99 is color-identity-legal for {cmds[0]} (§903.4)", ok and not offenders)
    # a negative control: a BLUE card is illegal in Magda's mono-RED identity.
    ok, off = bridge.color_identity_legal(["Magda, Brazen Outlaw"], ["Counterspell"], corpus)
    check("an off-identity card is rejected (negative control)", (not ok) and "Counterspell" in off)


# ---- cast from the command zone + the §903.8 tax -----------------------------------------------------

def _cast_state() -> dict:
    """alice has Magda ({1}{R}) in the command zone and four untapped Mountains; it's her precombat main."""
    s = bridge.make_deck_state(
        {"alice": ["Mountain"] * 4 + ["Goblin Piker"] * 95, "bob": ["Plains"] * 99},
        seed=0, variant="commander",
        commanders={"alice": ["Magda, Brazen Outlaw"], "bob": ["Isamaru, Hound of Konda"]},
    )
    # put four Mountains onto alice's battlefield untapped (so she can pay {1}{R} and the taxed recast).
    lands = sorted(c for (c,) in s["on_battlefield"]) + \
        sorted(c for (p, c) in s["in_hand"] if p == "alice" and (c, "land") in s.get("spell_type", set()))
    mtns = [c for (p, c) in (list(s["in_hand"]) + list(s["in_library"]))
            if p == "alice" and (c, "land") in s.get("spell_type", set())][:6]
    for m in mtns:
        s["in_hand"].discard(("alice", m)); s["in_library"].discard(("alice", m))
        s["on_battlefield"].add((m,)); s.setdefault("printed_control", set()).add(("alice", m))
    s["_lib_order"]["alice"] = [c for c in s["_lib_order"]["alice"] if c not in mtns]
    s["current_step"] = {("precombat_main",)}
    s["_land_played"] = {("alice",)}                  # we placed the lands directly; no further land drop
    _quiet(driver._refresh_mana_pool, s, "alice")
    return s


def _cast_and_tax() -> None:
    s = _cast_state()
    cmd = next(c for (p, c) in s["command_zone"] if p == "alice")
    castable = driver.can_cast_commander(s, "alice")
    check("the commander is castable from the command zone at sorcery speed with mana",
          cmd in castable)

    _quiet(driver.cast_commander, s, "alice", cmd, ["alice", "bob"])
    check("after casting, the commander is on the battlefield (it entered)",
          (cmd,) in s["on_battlefield"])
    check("after casting, the commander has left the command zone",
          ("alice", cmd) not in s["command_zone"])
    check("the recast count incremented to 1 (§903.8 bookkeeping)",
          s["_cmd_casts"].get(cmd) == 1)
    check("the next cast-from-command-zone is taxed {2} (1 prior cast)",
          driver._commander_tax(s, cmd) == 2)
    check("the commander's PRINTED cost was restored after the cast (tax is a surcharge, not a new cost)",
          (cmd, 1) in s.get("mana_generic", set()))   # Magda {1}{R}: generic part is 1


def _tax_escalates() -> None:
    s = _cast_state()
    cmd = next(c for (p, c) in s["command_zone"] if p == "alice")
    taxes = []
    for _ in range(3):
        s["_cmd_casts"][cmd] = s.get("_cmd_casts", {}).get(cmd, 0)
        taxes.append(driver._commander_tax(s, cmd))
        s["_cmd_casts"][cmd] += 1
    check("the recast tax escalates {0},{2},{4} with each prior cast (§903.8)", taxes == [0, 2, 4])


# ---- §903.9 / §704.5 commander replacement -----------------------------------------------------------

def _replacement() -> None:
    # a commander on the battlefield is taken to the graveyard by an SBA (toughness 0); its owner MAY
    # return it to the command zone instead. Drive the _choose seam both ways.
    def state_with_dying_commander():
        s = bridge.make_deck_state(
            {"alice": ["Mountain"] * 99, "bob": ["Plains"] * 99}, seed=0, variant="commander",
            commanders={"alice": ["Magda, Brazen Outlaw"], "bob": ["Isamaru, Hound of Konda"]},
        )
        cmd = next(c for (p, c) in s["command_zone"] if p == "alice")
        s["command_zone"].discard(("alice", cmd))
        s["on_battlefield"].add((cmd,))
        s["life"] = {("alice", 40), ("bob", 40)}
        return s, cmd

    # default (no policy) -> the standard line: return to the command zone.
    s, cmd = state_with_dying_commander()
    redirected = _quiet(driver._commander_replacement, s, cmd, "graveyard")
    check("by default a dying commander is returned to the command zone (§903.9)",
          redirected and ("alice", cmd) in s["command_zone"])
    check("the returned commander is NOT in the graveyard", (cmd,) not in s.get("graveyard", set()))

    # decline -> it goes where it was headed (graveyard); the caller does the move.
    s2, cmd2 = state_with_dying_commander()
    s2["_policy"] = lambda st, key, opts, default: False if key == "commander_replacement" else default
    redirected2 = _quiet(driver._commander_replacement, s2, cmd2, "graveyard")
    check("a player MAY decline the replacement (then it moves normally)",
          (not redirected2) and ("alice", cmd2) not in s2["command_zone"])

    # the full zone_change hook: an SBA death routes through _apply_outputs and the replacement fires.
    s3, cmd3 = state_with_dying_commander()
    s3["eff_toughness_override"] = set()              # ensure no stale override
    out = {"to_untap": set(), "to_draw": set(),
           "zone_change": {(cmd3, "battlefield", "graveyard")},
           "loses_game": set(), "player_damage": set(), "pending": set()}
    _quiet(driver._apply_outputs, s3, out, "alice")
    check("the §704.5 hook in _apply_outputs redirects a dying commander to the command zone",
          ("alice", cmd3) in s3["command_zone"] and (cmd3,) not in s3.get("graveyard", set()))


# ---- end-to-end 1v1 Commander game -------------------------------------------------------------------

def _full_game() -> None:
    # a complete Commander game through the referee (env, random policy) reaches a decisive result, and
    # along the way a commander is cast from the command zone (the recast count proves it).
    results = []
    cmd_cast_seen = False
    for seed in (1, 2, 3):
        s = game.new_commander_game(seed=seed)
        s["_policy"] = game.random_policy
        s = env.start(s)
        for _ in range(4000):
            if env.is_terminal(s):
                break
            acts = env.legal_actions(s)
            if not acts:
                break
            a = game.random_policy(s, "action", acts, acts[0])
            s = env.step(s, a)
        results.append(env.winner(s))
        if any(n > 0 for n in s.get("_cmd_casts", {}).values()):
            cmd_cast_seen = True
        check(f"both seats still tracked as players at end (seed {seed})",
              {p for (p,) in s["is_player"]} == {"alice", "bob"})
    check("at least one 1v1 Commander game reached a decisive result",
          any(w is not None for w in results))
    check("a commander was cast from the command zone during a real game", cmd_cast_seen)
    # the env offers a 'cast_commander' action when the commander is castable.
    s = game.new_commander_game(seed=1)
    s["_policy"] = game.random_policy
    s = env.start(s)
    offered = False
    for _ in range(4000):
        if env.is_terminal(s):
            break
        acts = env.legal_actions(s)
        if any(a[0] == "cast_commander" for a in acts):
            offered = True
            break
        if not acts:
            break
        s = env.step(s, game.random_policy(s, "action", acts, acts[0]))
    check("env surfaces a 'cast_commander' action when the commander is castable", offered)


def run() -> None:
    _setup()
    _color_identity()
    _cast_and_tax()
    _tax_escalates()
    _replacement()
    _full_game()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_win_search.py — the optimistic win-lookahead (win_search.find_win) over the full env.

A win reachable within the horizon (opponent passive) is found and the line is returned, so the agent can
"go for it". Covers a forced combat lethal and a spell-based ("you win the game") win — the same mechanism
as a Thassa's-Oracle combo. Run: python3 test_win_search.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root on sys.path (test relocated into subfolder)

import win_search

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _combat_lethal():
    # alice's main phase, a 5/5 untapped non-sick, bob at 4 with a library (no deck-out) and no blockers.
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 4)},
          "on_battlefield": {("ogre",)}, "printed_type": {("ogre", "creature")},
          "printed_power": {("ogre", 5)}, "printed_toughness": {("ogre", 5)}, "printed_control": {("alice", "ogre")},
          "in_hand": set(), "in_library": {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"bob": [f"b{i}" for i in range(20)]},
          "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    path, nodes = win_search.find_win(st, max_turns=2, node_budget=3000)
    check("combat: a forced lethal is found", path is not None)
    check("combat: the line attacks with the lethal creature",
          path is not None and any(a[0] == "attack" and "ogre" in a[1] for a in path))
    check("combat: found cheaply (narrow win = few nodes)", nodes < 50)


def _spell_win():
    # alice has a 0-cost 'you win the game' sorcery in hand (the Thassa's-Oracle win mechanism, minus the
    # ETB-trigger + colored-mana plumbing). The lookahead should find 'cast it -> win'.
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 1), ("bob", 40)},
          "in_hand": {("alice", "wincon")}, "in_library": {("bob", "b0")},
          "spell_type": {("wincon", "sorcery")}, "mana_cost": {("wincon", 0)},
          "mana_available": {("alice", 0), ("bob", 0)},
          "spell_effect": {("wincon", "win_game", 0, "controller")},
          "on_battlefield": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "_sick": set(), "_land_played": set()}
    path, nodes = win_search.find_win(st, me="alice", max_turns=2, node_budget=500)
    check("spell-win: a non-combat 'you win' line is found", path is not None)
    check("spell-win: the line casts the wincon",
          path is not None and any(a[0] == "cast" and a[2] == "wincon" for a in path))


def _no_false_win():
    # no win reachable: alice has nothing, bob healthy with a library -> find_win returns None (no fabricated win).
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 20)},
          "in_hand": set(), "in_library": {("alice", f"a{i}") for i in range(20)} | {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"alice": [f"a{i}" for i in range(20)], "bob": [f"b{i}" for i in range(20)]},
          "on_battlefield": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    path, _ = win_search.find_win(st, max_turns=3, node_budget=400)
    check("no-win: returns None when no win is reachable (no false positive)", path is None)


def _develop_state():
    """No win is reachable, but alice can DEVELOP toward a life_zero win: 3 untapped Islands + a 3/3 in hand."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "has_priority": {("alice",)},
        "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("isl1",), ("isl2",), ("isl3",)},
        "printed_control": {("alice", "isl1"), ("alice", "isl2"), ("alice", "isl3")},
        "printed_type": {("isl1", "land"), ("isl2", "land"), ("isl3", "land"), ("bear", "creature")},
        "land_produces": {("isl1", "blue"), ("isl2", "blue"), ("isl3", "blue")},
        "in_hand": {("alice", "bear")}, "instance_of": {("bear", "big_bear")},
        "spell_type": {("bear", "creature")}, "card_type": {("big_bear", "creature")},
        "printed_power": {("bear", 3)}, "printed_toughness": {("bear", 3)},
        "mana_cost": {("bear", 3)}, "mana_generic": {("bear", 3)},
        "on_stack": set(), "_stack_info": {}, "all_passed": set(), "tapped": set(), "counter": set(),
        "in_library": {("alice", f"a{i}") for i in range(10)} | {("bob", f"b{i}") for i in range(10)},
        "_lib_order": {"alice": [f"a{i}" for i in range(10)], "bob": [f"b{i}" for i in range(10)]},
    }


def _progress_checks():
    import env
    # progress_score rises monotonically toward an opponent losing on each §104 axis.
    def life(b):
        return {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", b)}}
    check("progress(life_zero): lower opponent life scores higher",
          win_search.progress_score(life(5), "alice", "life_zero")
          > win_search.progress_score(life(30), "alice", "life_zero"))
    pz = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", 40)},
          "counter": {("bob", "poison", 8)}}
    check("progress(poison_ten): opponent poison scores", win_search.progress_score(pz, "alice", "poison_ten") > 0)
    cd = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 40), ("bob", 40)},
          "commander_damage": {("bob", "cmd", 18)}}
    check("progress(commander_damage): accrued commander damage scores",
          win_search.progress_score(cd, "alice", "commander_damage") > 0)

    # find_progress DEVELOPS (casts a creature toward the life_zero win) rather than returning nothing.
    st = _develop_state()
    assert win_search.find_win(st, me="alice", max_turns=2, node_budget=2000)[0] is None   # no forced win
    path, score = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=4, node_budget=6000)
    check("find_progress returns a developing move (not pass)", bool(path) and path[0][0] == "cast")
    check("the developing move deploys the creature", bool(path) and path[0][2] == "bear")

    # FOLLOW-THROUGH: the default-horizon line doesn't just SET UP — it USES the creature (the cast-now,
    # attack-next-turn arc), so a just-cast 3/3 is valued by the damage it will deal, not as static board.
    deep, deep_sc = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=4, node_budget=6000)
    shallow, shallow_sc = win_search.find_progress(st, me="alice", axis="life_zero", max_turns=2, node_budget=6000)
    check("the develop line works toward the win — it attacks with the creature",
          any(a[0] == "attack" and a[1] for a in deep))
    check("seeing the follow-through scores higher than just setup", deep_sc > shallow_sc)

    # SYNERGY vs DIRECT progress, on a shared %-scale (the second evaluator wired in). A 5-card combo:
    # assembling pieces is CONVEX-discounted so a partial combo can't beat real win progress, but a near-
    # complete one can. Calibration matches the spec: 2/5 -> 16% (< a 25% damage move), 4/5 -> 64%.
    combo = {"slugs": {f"p{i}" for i in range(5)}, "size": 5}

    def assembled(k):
        pieces = [f"p{i}" for i in range(k)]
        return {"is_player": {("me",), ("opp",)}, "on_battlefield": {(c,) for c in pieces},
                "printed_control": {("me", c) for c in pieces}, "in_hand": set(),
                "instance_of": {(c, c) for c in pieces}}
    check("synergy: 2-of-5 combo ~ 16% (< a 25% win move)",
          15 < win_search.synergy_score(assembled(2), "me", combo) * 100 < 20)
    check("synergy: 4-of-5 combo ~ 64% (now worth chasing)",
          60 < win_search.synergy_score(assembled(4), "me", combo) * 100 < 68)
    check("synergy folds into progress_score (a near-combo outscores 1 chip of damage)",
          win_search.progress_score(assembled(4), "me", "alt_win", synergy=combo)
          > win_search.progress_score({"is_player": {("me",), ("opp",)}, "life": {("me", 20), ("opp", 19)}},
                                      "me", "life_zero", start_life=20))
    check("direct calibration: 1 damage in Standard = 5%",
          abs(win_search.progress_score({"is_player": {("me",), ("opp",)}, "life": {("me", 20), ("opp", 19)}},
                                        "me", "life_zero", start_life=20) - 5) < 0.5)

    # the policy: with an axis it develops; WITHOUT an axis it keeps the old win-or-defer behavior.
    s0 = env.start(_develop_state())
    acts = env.legal_actions(s0)
    dev_pol = win_search.win_seeking_policy(max_turns=2, node_budget=2000, axis="life_zero",
                                            progress_turns=3, progress_budget=4000)
    choice = dev_pol(s0, "action", acts, acts[0])
    check("policy with an axis develops (casts) instead of passing", choice is not None and choice[0] == "cast")
    plain_pol = win_search.win_seeking_policy(max_turns=2, node_budget=2000)   # axis=None
    plain = plain_pol(env.start(_develop_state()), "action", acts, acts[0])
    check("policy without an axis falls back (no development)", plain == acts[0])


def _defensive_opponent():
    # the search's opponent now BLOCKS to avoid lethal: a single attacker the defender can block away is NOT
    # a fabricated win, but lethal THROUGH the survival block still is.
    def base():
        return {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
                "current_step": {("precombat_main",)}, "in_hand": set(),
                "in_library": {("bob", f"b{i}") for i in range(20)}, "_lib_order": {"bob": [f"b{i}" for i in range(20)]},
                "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
                "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    a = base(); a["life"] = {("alice", 20), ("bob", 3)}
    a["on_battlefield"] = {("big",), ("blk",)}; a["printed_type"] = {("big", "creature"), ("blk", "creature")}
    a["printed_power"] = {("big", 5), ("blk", 2)}; a["printed_toughness"] = {("big", 5), ("blk", 2)}
    a["printed_control"] = {("alice", "big"), ("bob", "blk")}
    check("a single attacker the opponent can block is NOT a fabricated win",
          win_search.find_win(a, me="alice", max_turns=2, node_budget=3000)[0] is None)
    b = base(); b["life"] = {("alice", 20), ("bob", 3)}
    b["on_battlefield"] = {("b1",), ("b2",), ("blk",)}
    b["printed_type"] = {("b1", "creature"), ("b2", "creature"), ("blk", "creature")}
    b["printed_power"] = {("b1", 5), ("b2", 5), ("blk", 2)}; b["printed_toughness"] = {("b1", 5), ("b2", 5), ("blk", 2)}
    b["printed_control"] = {("alice", "b1"), ("alice", "b2"), ("bob", "blk")}
    check("lethal THROUGH a survival block is still found",
          win_search.find_win(b, me="alice", max_turns=2, node_budget=4000)[0] is not None)

    # REGRESSION (§510.2 combat damage is dealt ONCE): an UNBLOCKED but NON-LETHAL swing must not fabricate
    # a loss. The driver persists combat damage to life and then re-derives loses_game as a triggered-effect
    # backstop; if that re-run still saw the attack it would subtract the SAME damage twice (bob at 5 takes
    # 3 -> 2, then 2-3=-1 -> phantom death). The kill must come from REAL lethal, not double-counting.
    c = base(); c["life"] = {("alice", 20), ("bob", 5)}
    c["on_battlefield"] = {("atk",)}; c["printed_type"] = {("atk", "creature")}
    c["printed_power"] = {("atk", 3)}; c["printed_toughness"] = {("atk", 3)}
    c["printed_control"] = {("alice", "atk")}
    check("a non-lethal unblocked swing is NOT a fabricated win (no combat double-count)",
          win_search.find_win(c, me="alice", max_turns=2, node_budget=3000)[0] is None)
    d = base(); d["life"] = {("alice", 20), ("bob", 3)}      # same board, bob now actually in range
    d["on_battlefield"] = {("atk",)}; d["printed_type"] = {("atk", "creature")}
    d["printed_power"] = {("atk", 3)}; d["printed_toughness"] = {("atk", 3)}
    d["printed_control"] = {("alice", "atk")}
    check("the SAME 3-power attacker IS lethal at 3 life (real kill still found)",
          win_search.find_win(d, me="alice", max_turns=2, node_budget=3000)[0] is not None)


def _minimax_checks():
    # find_minimax: MAXIMIZE my win while the opponent MINIMIZES it (perfect information, hands revealed).
    def base(alife=20, blife=20):
        return {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
                "current_step": {("precombat_main",)}, "life": {("alice", alife), ("bob", blife)},
                "in_hand": set(),
                "in_library": {("alice", f"a{i}") for i in range(20)} | {("bob", f"b{i}") for i in range(20)},
                "_lib_order": {"alice": [f"a{i}" for i in range(20)], "bob": [f"b{i}" for i in range(20)]},
                "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
                "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}

    # a forced lethal is still found — and valued as a WIN.
    a = base(20, 4)
    a["on_battlefield"] = {("ogre",)}; a["printed_type"] = {("ogre", "creature")}
    a["printed_power"] = {("ogre", 5)}; a["printed_toughness"] = {("ogre", 5)}
    a["printed_control"] = {("alice", "ogre")}
    pa, va = win_search.find_minimax(a, me="alice", max_turns=2, node_budget=4000)
    check("minimax: a forced lethal is a WIN value", va > 10 ** 5)
    check("minimax: the winning line attacks with the lethal creature",
          pa and pa[0][0] == "attack" and "ogre" in pa[0][1])

    # the opponent BLOCKS to survive -> attacking trades, no fabricated win (realistic, bounded value).
    b = base(20, 4)
    b["on_battlefield"] = {("ogre",), ("wall",)}
    b["printed_type"] = {("ogre", "creature"), ("wall", "creature")}
    b["printed_power"] = {("ogre", 5), ("wall", 5)}; b["printed_toughness"] = {("ogre", 5), ("wall", 5)}
    b["printed_control"] = {("alice", "ogre"), ("bob", "wall")}
    _pb, vb = win_search.find_minimax(b, me="alice", max_turns=2, node_budget=4000)
    check("minimax: an opponent that can block to survive is NOT a fabricated win", -10 ** 5 < vb < 10 ** 5)

    # SELF-INTERESTED OPPONENT (max-n, not zero-sum): the opponent plays for ITS OWN win and does NOT spend
    # moves purely to deny my development — so a creature I develop scores on its own merits instead of being
    # 'answered' to flat by a griefing opponent. From the develop state (a 3/3 in hand + mana, benign board)
    # minimax DEVELOPS rather than holding back. (The old zero-sum minimax would discount the cast because the
    # opponent was modeled as trading it away.)
    import test_win_search  # noqa: F401 (self) — reuse the develop fixture below via the module-level helper
    dev = _develop_state()
    pdev, _vdev = win_search.find_minimax(dev, me="alice", my_axis="life_zero", opp_axis="life_zero",
                                          max_turns=2, node_budget=4000)
    check("minimax: a self-interested opponent does NOT grief development — it develops (casts)",
          bool(pdev) and pdev[0][0] == "cast")

    # but the opponent STILL races to its OWN win: facing an unanswerable lethal board (bob's 10/10, alice at
    # 3 with no blocker), the value is a loss — the opponent takes the kill (immediate wins are still pursued).
    L = base(3, 20)
    L["on_battlefield"] = {("big",)}; L["printed_type"] = {("big", "creature")}
    L["printed_power"] = {("big", 10)}; L["printed_toughness"] = {("big", 10)}
    L["printed_control"] = {("bob", "big")}
    _pl, vl = win_search.find_minimax(L, me="alice", max_turns=2, node_budget=4000)
    check("minimax: the opponent still races to its OWN win (unanswerable lethal -> a loss value)", vl < -10 ** 5)


def _forced_adversarial():
    """find_win(forced=True): the win must hold against the opponent's WORST-CASE block (forall over blocks),
    not the survival/default block. It preserves REAL lethals and is strictly conservative (never accepts a win
    the passive search rejects), so a takeover gate built on it can't fabricate."""
    # a clean unblockable lethal: alice 5/5, bob at 4, NO blockers -> found by BOTH passive and forced.
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 4)}, "on_battlefield": {("ogre",)}, "printed_type": {("ogre", "creature")},
          "printed_power": {("ogre", 5)}, "printed_toughness": {("ogre", 5)}, "printed_control": {("alice", "ogre")},
          "in_hand": set(), "in_library": {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"bob": [f"b{i}" for i in range(20)]}, "tapped": set(), "counter": set(),
          "attacks": set(), "blocks": set(), "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    check("forced still finds a clean unblockable lethal (real wins preserved)",
          win_search.find_win(st, me="alice", max_turns=1, node_budget=3000, forced=True)[0] is not None)
    # conservatism: across a handful of boards, a forced win implies a passive win (forced ⊆ passive) — it is
    # strictly stricter, so it can only REMOVE fabricated wins, never invent one.
    boards = []
    for opp_life, blk in [(4, None), (3, 2), (6, 4), (8, None)]:
        b = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
             "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", opp_life)},
             "on_battlefield": {("ogre",)} | ({("blk",)} if blk else set()),
             "printed_type": {("ogre", "creature")} | ({("blk", "creature")} if blk else set()),
             "printed_power": {("ogre", 5)} | ({("blk", blk)} if blk else set()),
             "printed_toughness": {("ogre", 5)} | ({("blk", blk)} if blk else set()),
             "printed_control": {("alice", "ogre")} | ({("bob", "blk")} if blk else set()),
             "in_hand": set(), "in_library": {("bob", f"b{i}") for i in range(20)},
             "_lib_order": {"bob": [f"b{i}" for i in range(20)]}, "tapped": set(), "counter": set(),
             "attacks": set(), "blocks": set(), "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
        boards.append(b)
    subset = all(win_search.find_win(b, me="alice", max_turns=2, node_budget=3000, forced=False)[0] is not None
                 for b in boards
                 if win_search.find_win(b, me="alice", max_turns=2, node_budget=3000, forced=True)[0] is not None)
    check("forced ⊆ passive (every forced win is also a passive win — strictly conservative)", subset)


def _nearest_win():
    """find_nearest_win counts TURNS (env _turn-passes), not actions — a win THIS turn is `turns==0` no matter
    how many spells it takes (the rules-agnostic, instant-speed-friendly metric). bob at 40 (no combat lethal
    this turn) with a 0-cost 'you win' spell -> the only turn-0 win casts the wincon."""
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)}, "current_step": {("precombat_main",)},
          "life": {("alice", 20), ("bob", 40)},
          "on_battlefield": {("ogre",)}, "printed_type": {("ogre", "creature")},
          "printed_power": {("ogre", 5)}, "printed_toughness": {("ogre", 5)}, "printed_control": {("alice", "ogre")},
          "in_hand": {("alice", "wincon")}, "in_library": {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"bob": [f"b{i}" for i in range(20)]},
          "spell_type": {("wincon", "sorcery")}, "mana_cost": {("wincon", 0)},
          "spell_effect": {("wincon", "win_game", 0, "controller")}, "mana_available": {("alice", 0), ("bob", 0)},
          "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(), "_sick": set(), "_land_played": set()}
    near, turns, _ = win_search.find_nearest_win(st, me="alice", max_turns=6, node_budget=4000)
    check("nearest: a win is found", near is not None)
    check("nearest: a this-turn win is turns==0 (counts turns, not actions)", turns == 0)
    check("nearest: the turn-0 line casts the wincon",
          near is not None and any(a[0] == "cast" and a[2] == "wincon" for a in near))
    fn, ft, _ = win_search.find_nearest_win(st, me="alice", max_turns=6, node_budget=4000, forced=True)
    check("nearest(forced): the this-turn win is still found at turns==0", fn is not None and ft == 0)
    # no false positive: an empty board has no win at any horizon.
    empty = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
             "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", 20)},
             "in_hand": set(), "in_library": {("bob", "b0")}, "_lib_order": {"bob": ["b0"]},
             "on_battlefield": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
             "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    check("nearest: no fabricated win on an empty board",
          win_search.find_nearest_win(empty, me="alice", max_turns=4, node_budget=400)[0] is None)


def _nearest_multiturn():
    """find_nearest_win reaches a MULTI-turn combat kill; ordering doesn't change the result (complete, just
    faster); a beam finds the same win exploring no more nodes (the deeper-reach lever, fixed to keep `pass`
    so it can't cut the path to combat)."""
    cs = [f"c{i}" for i in range(3)]
    st = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
          "current_step": {("precombat_main",)}, "life": {("alice", 20), ("bob", 45)},   # 3x5=15/turn -> 3 turns
          "on_battlefield": {(c,) for c in cs}, "printed_type": {(c, "creature") for c in cs},
          "printed_power": {(c, 5) for c in cs}, "printed_toughness": {(c, 5) for c in cs},
          "printed_control": {("alice", c) for c in cs}, "in_hand": set(),
          "in_library": {("alice", f"a{i}") for i in range(20)} | {("bob", f"b{i}") for i in range(20)},
          "_lib_order": {"alice": [f"a{i}" for i in range(20)], "bob": [f"b{i}" for i in range(20)]},
          "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
          "mana_available": {("alice", 0), ("bob", 0)}, "_sick": set()}
    p_o, turns_o, n_o = win_search.find_nearest_win(st, me="alice", max_turns=8, node_budget=8000, order=True)
    _p_r, turns_r, _ = win_search.find_nearest_win(st, me="alice", max_turns=8, node_budget=8000, order=False)
    check("nearest reaches a 3-attack (turns=4) combat kill", p_o is not None and turns_o == 4)
    check("ordering is complete (same nearest turn-distance as unordered)", turns_r == turns_o)
    p_b, turns_b, n_b = win_search.find_nearest_win(st, me="alice", max_turns=8, node_budget=8000, beam=3)
    check("beam finds the same win (turns=4) with no more nodes", p_b is not None and turns_b == 4 and n_b <= n_o)


def _enhanced_player():
    """EnhancedLookaheadPlayer drives a full game (plays the nearest win when one is in reach, else a
    don't-blunder fallback)."""
    import contextlib
    import io
    from mtg.lookahead import EnhancedLookaheadPlayer
    from mtg.players import RandomPlayer, play
    bot = EnhancedLookaheadPlayer(max_turns=4, node_budget=600, forced=True, seed=0)
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": bot, "bob": RandomPlayer(seed=1)}, seed=3, max_moves=400)
    check("EnhancedLookaheadPlayer plays a full game to a terminal result", g.is_game_over())


def run():
    _combat_lethal()
    _spell_win()
    _no_false_win()
    _progress_checks()
    _defensive_opponent()
    _minimax_checks()
    _forced_adversarial()
    _nearest_win()
    _nearest_multiturn()
    _enhanced_player()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""test_society_of_control.py — the SocietyOfControlPlayer control bot (mtg/society_of_control.py).

Focus: burn_choice — cast a CREATURE-TARGETING damage spell ONLY when it would KILL an opponent creature
(strictly target creature, not 'any target'; never fired speculatively; weighted by the biggest threat it can
remove). Damage spells pick their target at resolution, so burn_choice is a CAST-TIME decision keyed on whether
a lethal target exists. Run as a script; exits non-zero on any failure.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from mtg.game import Game
from mtg.models import PriorityOption as Do
from mtg.predicates import is_cantrip, is_commander_cast, is_creature, is_draw_ability, is_mana_rock
from mtg.society_of_control import SocietyOfControlPlayer

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


def _game(*, hand, effects, creatures):
    """A minimal engine state: `hand` = [(inst, slug)], `effects` = {slug: (amount, scope)} deal_damage facts,
    `creatures` = [(power, toughness, controller)] or [(power, toughness, controller, is_commander)] battlefield."""
    st = {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
        "in_hand": {("alice", i) for i, _ in hand},
        "instance_of": {(i, s) for i, s in hand},
        "card_ability": {(s, "a0", "spell") for s in effects},
        "card_effect": {(s, "a0", 0, "deal_damage", str(a), scope, "-", "-") for s, (a, scope) in effects.items()},
        "on_battlefield": set(), "printed_control": set(), "printed_type": set(),
        "printed_power": set(), "printed_toughness": set(), "is_commander": set(),
    }
    for n, spec in enumerate(creatures):
        p, t, ctrl = spec[:3]
        cid = f"c{n}"
        st["instance_of"] |= {(cid, "grizzly_bears")}
        st["on_battlefield"] |= {(cid,)}
        st["printed_control"] |= {(ctrl, cid)}
        st["printed_type"] |= {(cid, "creature")}
        st["printed_power"] |= {(cid, p)}
        st["printed_toughness"] |= {(cid, t)}
        if len(spec) > 3 and spec[3]:
            st["is_commander"] |= {(cid,)}
    return Game.from_state(st)


def _burn(g, card, target=None):
    """burn_choice score for casting `card`; `target` (a creature instance id) selects the per-target cast
    variant the engine would enumerate (None = no cast-time target -> scored by the best creature on board)."""
    p = SocietyOfControlPlayer(); p.bind(g, "alice")
    choices = {"target": target} if target is not None else {}
    return p.burn_choice(g, NS(kind="cast", card=NS(id=card), choices=choices))


def run() -> None:
    BOMBARD = {"bombard": (4, "target_creature")}                 # 4 dmg, strictly target creature
    BOLT = {"lightning_bolt": (3, "any_target")}                  # 3 dmg, ANY target (face burn)
    NEG = float("-inf")

    # ── FIRES only on an actual kill ───────────────────────────────────────────────────────────────────────
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (5, 5, "bob")])
    check("burn_choice fires on a lethal target (4 dmg kills the 2/2)", _burn(g, "b1") > 0)
    check("burn_choice -inf at the un-killable 5/5 variant (5 toughness > 4 dmg)", _burn(g, "b1", "c1") == NEG)
    check("burn_choice fires at the killable 2/2 variant", _burn(g, "b1", "c0") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob")])
    check("burn_choice does NOT fire when nothing is killable (no speculative cast)", _burn(g, "b1") == NEG)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "alice")])
    check("burn_choice ignores OUR own creatures", _burn(g, "b1") == NEG)

    g = _game(hand=[("lb", "lightning_bolt")], effects=BOLT, creatures=[(2, 2, "bob")])
    check("burn_choice EXCLUDES 'any target' spells (face burn, handled elsewhere)", _burn(g, "lb") == NEG)

    g = _game(hand=[("b1", "bombard")], effects={}, creatures=[(2, 2, "bob")])
    check("burn_choice is inert when card rules aren't loaded (no card_effect -> -inf)", _burn(g, "b1") == NEG)

    # ── TARGET PRIORITY: commander > power > toughness > arbitrary ──────────────────────────────────────────
    # commander beats a far bigger non-commander (backup answer in hand so the reserve rule doesn't hold it)
    g = _game(hand=[("b1", "bombard"), ("b2", "bombard")], effects=BOMBARD,
              creatures=[(1, 1, "bob", True), (4, 4, "bob")])
    check("priority: COMMANDER (1/1) outranks a bigger non-commander (4/4)", _burn(g, "b1", "c0") > _burn(g, "b1", "c1"))

    # commander tier only applies when the spell can ACTUALLY kill the commander. A 4-dmg spell at a 6/6
    # commander must NOT bend the priority — the commander variant doesn't even fire, and the best killable
    # body (here the 2/2 by power) is chosen instead. (Mirrors the real game: Molten Exhale 4 vs Ovika 6/6.)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(6, 6, "bob", True), (2, 2, "bob"), (1, 4, "bob")])
    check("un-killable COMMANDER (6/6 vs 4 dmg) doesn't fire / doesn't bend priority", _burn(g, "b1", "c0") == NEG)
    check("...and the best killable body is targeted instead (the 2/2)", _burn(g, "b1", "c1") > _burn(g, "b1", "c2"))

    # among non-commanders, higher POWER wins even with lower toughness (no creature of ours answers it)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 1, "bob"), (1, 4, "bob")])
    check("priority: higher POWER (3/1) outranks higher toughness (1/4)", _burn(g, "b1", "c0") > _burn(g, "b1", "c1"))

    # ...UNLESS the biggest-power killable creature is one we could block-and-kill in combat: then burn the
    # high-TOUGHNESS body combat can't answer. Our 3/3 blocks-and-kills the 2/2, so removal hits the 1/4.
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (1, 4, "bob"), (3, 3, "alice")])
    check("combat-answerable top threat -> burn the high-TOUGHNESS body (1/4 over 2/2)",
          _burn(g, "b1", "c1") > _burn(g, "b1", "c0"))
    # a TAPPED would-be blocker can't block, so the swap does NOT apply -> stays power-first (burn the 2/2)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (1, 4, "bob"), (3, 3, "alice")])
    g.state.setdefault("tapped", set()).add(("c2",))           # tap our 3/3 (c2) so it can't block
    check("a TAPPED blocker doesn't count as a combat answer -> power-first (burn the 2/2)",
          _burn(g, "b1", "c0") > _burn(g, "b1", "c1"))

    # equal power -> higher TOUGHNESS wins
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (2, 4, "bob")])
    check("priority: equal power -> higher TOUGHNESS (2/4 over 2/2)", _burn(g, "b1", "c1") > _burn(g, "b1", "c0"))

    # equal power AND toughness -> both fire, broken by a stable arbitrary jitter (distinct, deterministic)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (2, 2, "bob")])
    s0, s1 = _burn(g, "b1", "c0"), _burn(g, "b1", "c1")
    check("priority: identical P/T both fire", s0 > 0 and s1 > 0)
    check("priority: identical P/T broken by an arbitrary (distinct) jitter", s0 != s1)
    check("priority: the jitter is deterministic (stable across calls)", _burn(g, "b1", "c0") == s0)

    # no cast-time target -> scored by the BEST creature on board (commander here)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(4, 4, "bob"), (1, 1, "bob", True)])
    check("no-target fallback scores by the best creature (the commander)",
          _burn(g, "b1") == _burn(g, "b1", "c1"))

    # ── COMMANDER RESERVE: hold the only commander-answer; spend it only with a backup in hand ──────────────
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])  # commander, only answer
    check("burn_choice reserves our ONLY commander-answer (don't spend the last one)", _burn(g, "b1", "c0") == NEG)

    g = _game(hand=[("b1", "bombard"), ("b2", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])
    check("burn_choice kills the commander when a BACKUP answer is in hand", _burn(g, "b1", "c0") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob", True), (2, 2, "bob")])
    check("commander un-killable by this spell -> reserve rule off, kills the 2/2", _burn(g, "b1", "c1") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True), (2, 2, "bob")])
    check("only commander-answer is held even with another creature killable (reserve holds the whole spell)",
          _burn(g, "b1", "c1") == NEG)

    # FORCED-WIN TAKE-OVER: a lethal line this turn is taken over any positional play.
    def _burn_to_face(opp_life, dmg=3, mana=5):
        # both players keep a library so a non-lethal sim doesn't auto-advance into a spurious deck-out loss
        lib = {(pl, f"{pl}_lib{i}") for pl in ("alice", "bob") for i in range(20)}
        st = {
            "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", opp_life)},
            "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
            "in_hand": {("alice", "bolt_1")}, "in_library": lib,
            "instance_of": {("bolt_1", "lightning_bolt")} | {(c, "forest") for (_p, c) in lib},
            "card_ability": {("lightning_bolt", "a0", "spell")},
            "card_effect": {("lightning_bolt", "a0", 0, "deal_damage", str(dmg), "any_target", "-", "-")},
            "spell_type": {("bolt_1", "instant")}, "free_grant": {("alice", "bolt_1")},
            "mana_available": {("alice", mana)},
        }
        g = Game.from_state(st); p = SocietyOfControlPlayer(); p.bind(g, "alice")
        return g, p

    g, p = _burn_to_face(2)                                       # 3-damage bolt, opponent at 2 -> lethal
    check("is_win_forceable True when a lethal burn is available", p.is_win_forceable(g) is True)
    win = p.force_win(g)
    check("force_win returns the lethal cast move", getattr(win, "kind", None) == "cast")
    check("choose_move TAKES the forced win (not a positional play)",
          getattr(p.choose_move(g), "kind", None) == "cast")

    g, p = _burn_to_face(5)                                       # 3-damage bolt, opponent at 5 -> NOT lethal
    check("is_win_forceable False when no lethal line exists", p.is_win_forceable(g) is False)
    check("force_win returns None when not lethal", p.force_win(g) is None)

    g, p = _burn_to_face(40, mana=0)                              # out of reach -> the cheap gate skips the scan
    check("force_win None (and gate skips) when the opponent is out of reach", p.force_win(g) is None)

    # choose_x placeholder: lethal preferred when affordable, else max affordable
    _, p = _burn_to_face(2)
    check("choose_x picks the lethal value when affordable", p.choose_x(g, lethal=4, affordable=6) == 4)
    check("choose_x falls back to max affordable when lethal is out of reach", p.choose_x(g, lethal=9, affordable=6) == 6)

    # MANA ROCKS / DORKS deployed before other creatures (ramp first).
    def _ramp_game():
        st = {
            "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
            "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
            "in_hand": {("alice", x) for x in ("rock", "bear", "dork", "bolt")},
            "instance_of": {("rock", "mind_stone"), ("bear", "grizzly_bears"),
                            ("dork", "llanowar_elves"), ("bolt", "lightning_bolt")},
            "mana_ability": {("mind_stone", "{T}"), ("llanowar_elves", "{T}"),
                             ("chromatic_star", "{1}, {T}, Sacrifice ~")},
            "spell_type": {("rock", "artifact"), ("bear", "creature"), ("dork", "creature"), ("bolt", "instant")},
            "card_ability": {(s, "a0", "spell") for s in ("mind_stone", "grizzly_bears", "llanowar_elves")},
            "free_grant": {("alice", x) for x in ("rock", "bear", "dork", "bolt")},
            "mana_cost": {("rock", 2), ("bear", 2), ("dork", 1), ("bolt", 1)},
        }
        g = Game.from_state(st); p = SocietyOfControlPlayer(); p.bind(g, "alice")
        return g, p
    g, p = _ramp_game()
    def mc(c): return NS(kind="cast", card=NS(id=c), choices={})
    # is_mana_rock is the OBJECTIVE matcher the ramp line keys on (Do.SPELLS.matching(is_mana_rock)); it's true
    # of a tap-for-mana ROCK or DORK, false of a plain creature or a non-permanent — deck-independent.
    check("is_mana_rock True for a mana ROCK (Mind Stone artifact)", is_mana_rock(g, mc("rock")) is True)
    check("is_mana_rock True for a mana DORK (a creature that taps for mana)", is_mana_rock(g, mc("dork")) is True)
    check("is_mana_rock False for a plain creature", is_mana_rock(g, mc("bear")) is False)
    check("is_mana_rock False for a non-permanent spell", is_mana_rock(g, mc("bolt")) is False)
    check("curve_choice scores a matched mana source (above the floor=0.0 gate)", p.curve_choice(g, mc("rock")) > 0)
    check("choose_move deploys a mana source BEFORE the plain creature",
          getattr(getattr(p.choose_move(g), "card", None), "id", None) in ("rock", "dork"))

    # BRAWL/COMMANDER: cast the commander before the cheapest creature. The ladder line keys on the
    # cast_commander MOVE (is_commander_cast), placed above the is_creature curve line — so when the engine
    # surfaces a castable commander it's taken first. Verified on a real commander game (the move only exists
    # in commander-style variants).
    gc = Game(variant="commander", commanders={"alice": ["Grizzly Bears"], "bob": ["Grizzly Bears"]}, seed=3)
    check("commander game surfaces a castable commander at the opener",
          any(getattr(m, "kind", None) == "cast_commander" for m in gc.legal_moves))
    pc = SocietyOfControlPlayer().bind(gc, "alice")
    check("choose_move CASTS the commander (Brawl) when it can", getattr(pc.choose_move(gc), "kind", None) == "cast_commander")

    # ordering guarantee, in isolation: the commander line precedes the creature line, so a cast_commander
    # move wins over a cheaper plain-creature cast even though the creature curves cheaper.
    commander = NS(kind="cast_commander", card=NS(id="cmd", has_type=lambda t: t == "creature"), choices={})
    cheaper = NS(kind="cast", card=NS(id="bear", has_type=lambda t: t == "creature"), choices={})
    pr = NS(spells=[cheaper, commander])
    ladder = [Do.SPELLS.matching(is_commander_cast).prefer(lambda gg, m: 1.0, floor=0.0),
              Do.SPELLS.matching(is_creature).prefer(lambda gg, m: 1.0, floor=0.0)]
    picked = next((mv for opt in ladder if (mv := opt.pick(None, pr)) is not None), None)
    check("commander line is taken before the cheaper-creature line", picked is commander)

    # BOARD-COSTING DRAW (sac-and-draw, e.g. Insolent Neonate): develop_choice refuses it (board loss), so the
    # draw_ability_choice exception only fires it when (a) a CONTINUOUS draw engine is already in play and (b) if
    # it's a rummage, we hold an EXCESS LAND to pitch (a land in hand once we control five). Mirrors the real T9.
    def _draw_game(*, lands_in_play, land_in_hand, engine, sac=True, discard=True):
        st = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
              "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
              "in_hand": set(), "instance_of": set(), "on_battlefield": set(), "printed_control": set(),
              "printed_type": set(), "printed_power": set(), "printed_toughness": set(), "is_commander": set(),
              "card_ability": set(), "ability_cost": set(), "card_effect": set()}
        st["instance_of"] |= {("neo", "neonate")}; st["on_battlefield"] |= {("neo",)}
        st["printed_control"] |= {("alice", "neo")}; st["printed_type"] |= {("neo", "creature")}
        st["printed_power"] |= {("neo", 1)}; st["printed_toughness"] |= {("neo", 1)}
        st["card_ability"] |= {("neonate", "a1", "activated")}
        cost = ("Discard a card, Sacrifice ~" if discard else "Sacrifice ~") if sac else "{T}, Discard a card"
        st["ability_cost"] |= {("neonate", "a1", cost)}
        st["card_effect"] |= {("neonate", "a1", 0, "draw", "1", "you", "-", "-")}
        if engine:                                                 # Byway-like TRIGGERED draw engine (continuous)
            st["instance_of"] |= {("byw", "byway")}; st["on_battlefield"] |= {("byw",)}
            st["printed_control"] |= {("alice", "byw")}; st["printed_type"] |= {("byw", "creature")}
            st["printed_power"] |= {("byw", 3)}; st["printed_toughness"] |= {("byw", 3)}
            st["card_ability"] |= {("byway", "a1", "triggered")}
            st["card_effect"] |= {("byway", "a1", 1, "draw", "2", "you", "-", "if_you_did")}
        for i in range(lands_in_play):
            L = f"L{i}"; st["instance_of"] |= {(L, "mountain")}; st["on_battlefield"] |= {(L,)}
            st["printed_control"] |= {("alice", L)}; st["printed_type"] |= {(L, "land")}
        if land_in_hand:
            st["in_hand"] |= {("alice", "hl")}; st["instance_of"] |= {("hl", "mountain")}
            st["printed_type"] |= {("hl", "land")}
        g = Game.from_state(st); p = SocietyOfControlPlayer().bind(g, "alice")
        return g, p

    def draw_score(**kw):
        g, p = _draw_game(**kw)
        return p.draw_ability_choice(g, NS(kind="activate", card=NS(id="neo"), choices={}, ability=None))

    check("is_draw_ability True for an activated draw ability",
          is_draw_ability(_draw_game(lands_in_play=0, land_in_hand=False, engine=False)[0],
                          NS(kind="activate", card=NS(id="neo"), choices={})) is True)
    check("sac-and-draw FIRES with an engine + an excess land to pitch (>=5 lands, land in hand)",
          draw_score(lands_in_play=5, land_in_hand=True, engine=True) > 0)
    check("sac-and-draw HELD when the rummage has nothing to pitch (no land in hand)",
          draw_score(lands_in_play=5, land_in_hand=False, engine=True) == NEG)
    check("sac-and-draw HELD when a land isn't excess yet (only four lands)",
          draw_score(lands_in_play=4, land_in_hand=True, engine=True) == NEG)
    check("sac-and-draw HELD with no separate continuous draw engine",
          draw_score(lands_in_play=5, land_in_hand=True, engine=False) == NEG)
    check("a sac-draw with NO discard cost ignores the pitch caveat (fires on the engine alone)",
          draw_score(lands_in_play=0, land_in_hand=False, engine=True, discard=False) > 0)
    check("a draw ability that KEEPS its body is left to develop_choice (-inf here)",
          draw_score(lands_in_play=5, land_in_hand=True, engine=True, sac=False) == NEG)

    # FREE ONE-DROP CANTRIPS: with a commander in play whose on-cast trigger REFUNDS mana (Electro: add {R} on an
    # instant/sorcery), a one-drop cantrip is effectively free (the {1} comes back, it replaces itself), so the
    # free_cantrip_choice line casts it FIRST. Inert without such a commander, for non-one-drops, or off-type.
    def _cantrip_game(*, electro, mv=1, spell_type="instant", draws=True):
        st = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
              "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
              "in_hand": {("alice", "wisp")}, "instance_of": {("wisp", "crimson_wisps")},
              "on_battlefield": set(), "printed_control": set(), "printed_type": {("wisp", spell_type)},
              "printed_power": set(), "printed_toughness": set(), "is_commander": set(),
              "card_ability": set(), "ability_trigger": set(), "card_effect": set(), "mana_cost": {("wisp", mv)}}
        if draws:
            st["card_effect"] |= {("crimson_wisps", "a1", 0, "draw", "1", "you", "-", "-")}
        if electro:                                                # a refund commander: add {R} on instant/sorcery
            st["instance_of"] |= {("el", "electro")}; st["on_battlefield"] |= {("el",)}
            st["printed_control"] |= {("alice", "el")}; st["printed_type"] |= {("el", "creature")}
            st["printed_power"] |= {("el", 2)}; st["printed_toughness"] |= {("el", 3)}
            st["is_commander"] |= {("el",)}
            st["card_ability"] |= {("electro", "a2", "triggered")}
            st["ability_trigger"] |= {("electro", "a2", "you_cast_an_instant_or_sorcery_spell")}
            st["card_effect"] |= {("electro", "a2", 0, "add_mana", "1", "you", "red", "-")}
        g = Game.from_state(st); p = SocietyOfControlPlayer().bind(g, "alice")
        return g, p

    def cantrip_score(**kw):
        g, p = _cantrip_game(**kw)
        mv_move = NS(kind="cast", card=NS(id="wisp", has_type=lambda t, tt=kw.get("spell_type", "instant"): t == tt),
                     choices={})
        return p.free_cantrip_choice(g, mv_move)

    check("is_cantrip True for a cast spell that draws",
          is_cantrip(_cantrip_game(electro=False)[0], NS(kind="cast", card=NS(id="wisp"), choices={})) is True)
    check("free cantrip FIRES: refund commander in play + a one-drop instant cantrip", cantrip_score(electro=True) > 0)
    check("free cantrip inert with NO refund commander in play", cantrip_score(electro=False) == NEG)
    check("free cantrip inert for a non-one-drop (mv 2)", cantrip_score(electro=True, mv=2) == NEG)
    check("free cantrip inert off-type (Electro refunds instant/sorcery, not a creature)",
          cantrip_score(electro=True, spell_type="creature") == NEG)

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()

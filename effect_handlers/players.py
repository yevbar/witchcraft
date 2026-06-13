"""effect_handlers/players.py — PLAYER-SCOPED state & resource effects (controller / each opponent).

Own these cards.dl effect verbs (each targets a PLAYER or a resource a player owns, so they fit the
player-scoped trigger_effect path with target in {'controller','each_opponent'} and need NO engine change):
  - sacrifice  (a player sacrifices n creatures — pick the weakest deterministically; move to graveyard)
  - get_energy (an {E} energy resource a player holds, kept in a driver-side state relation)
  - win_game / lose_game  (set the game result the way the driver ends a game)
  - set_life   ('your/each opponent's life total becomes N' — a player-scoped life set)

MODEL
  sacrifice: amt is the COUNT, tgt is the class/scope. We only resolve the faithful, choice-free case
    'a player sacrifices N CREATURES of their own choosing': pick the N lowest-power creatures that player
    controls (ties broken by id) from the engine's DERIVED controls/creature/power, and move them to the
    graveyard via D._to_graveyard after firing any 'when ~ is sacrificed' look-back trigger (D._sacrifice).
    target='controller' (the controller sacrifices) or 'each_opponent' ('each opponent sacrifices a
    creature'). ABSTAIN on a SPECIFIC/named/subtyped/conditional class ('a Food', 'another Dragon', 'all
    creatures', 'any number of …', a named permanent), a non-creature class (land/artifact/permanent), and
    variable amounts ('X creatures', '1_per_…') — those need a choice or count the engine can't supply.

  get_energy: amt is a plain int -> the player gains N {E}. Stored in a driver-side state relation
    state['energy'] = {(player, total)}; read back / accumulated in apply. ABSTAIN on variable amounts.

  win_game / lose_game: ASSERT the game result the ENGINE turns into the §704.5 state-based win/loss.
    'you win the game' (Thassa's Oracle, Approach of the Second Sun, Felidar Sovereign, Test of Endurance)
    asserts eff_win_game(controller); 'you lose'/'target player loses'/'each opponent loses' asserts
    eff_lose_game(player). The engine derives wins_game(P)/loses_game(P) from these (§104.2a/§104.3a), and
    driver._apply_outputs ends the game on that derivation — the effect verb authors no game-ending logic
    itself, it just states the fact. These carry no condition — the engine doesn't model effect conditions
    (the bridge already drops every trigger's condition), so this matches that bar.

Faithful-or-abstain: encode -> None for anything we can't resolve correctly. See
effect_handlers/__init__.py for the @encoder / @applier contract and the driver helpers on D.
"""

from effect_handlers import encoder, applier


def _int(amt):
    """A plain integer amount, or None for '-', variable ('x_creatures', '1_per_…'), etc."""
    s = str(amt)
    return int(s) if s.lstrip("-").isdigit() else None


# ----- target slug -> player-scope vocabulary -------------------------------------------------------
# 'you'/'self'/'its_controller' = the ability's controller; opponent/that-player/each-player = others.
_SELF_TGT = {"you", "self", "its_controller", "controller"}


def _player_scope(tgt):
    """Map an effect target slug to 'controller' / 'each_opponent', or None if it isn't a clean,
    choice-free PLAYER scope (a specific named player, 'target player with exactly …', etc.)."""
    t = str(tgt)
    if t in _SELF_TGT:
        return "controller"
    # 'each opponent', 'each other player', 'each player', 'that player', 'target opponent/player'
    if t in ("each_opponent", "each_other_player", "each_player", "that_player",
             "target_opponent", "target_player"):
        return "each_opponent"
    return None


# ----- sacrifice ------------------------------------------------------------------------------------
# The only choice-free creature classes we resolve: a generic 'creature' the player sacrifices of their
# own choosing (we pick the weakest). A specific subtype / named / non-creature / 'all'/'any number' /
# conditional class abstains (needs a choice or selects a different permanent kind than we'd pick).
_SAC_CREATURE_CLASSES = {
    "a_creature", "another_creature", "a_nontoken_creature", "another_creature_you_control",
    "a_creature_of_their_choice", "a_nontoken_creature_of_their_choice",
}


@encoder("sacrifice")
def encode_sacrifice(verb, amt, tgt, extra):
    # amt = COUNT (default 1 when the count is implicit and the class names a single creature);
    # the class lives in tgt for controller-scoped ('Sacrifice a creature') and in extra for
    # player-targeted ('each opponent sacrifices a creature of their choice').
    if str(tgt) in ("self", "it"):                            # §701.16 'Sacrifice this permanent' (the source)
        return ("sacrifice_self", 0, "-")
    if str(extra) not in ("-", "None") and _player_scope(tgt):
        scope, cls = _player_scope(tgt), str(extra)
        n = _int(amt)
        n = 1 if n is None and cls in _SAC_CREATURE_CLASSES else n
    else:
        scope, cls = "controller", str(tgt)
        n = _int(amt)
        n = 1 if n is None and cls in _SAC_CREATURE_CLASSES else n
    if scope is None or n is None or n < 1 or cls not in _SAC_CREATURE_CLASSES:
        return None
    return ("sacrifice", n, scope)


@applier("sacrifice")
def apply_sacrifice(D, state, a, n, tgt, src, ctrl):
    """The chosen player(s) sacrifice their n weakest creatures (lowest power, ties by id) — a faithful
    deterministic 'sacrifice a creature of their choice'. Uses the engine's DERIVED controls/creature/
    power so a creature that entered via casting counts, and D._sacrifice so any 'when ~ is sacrificed'
    look-back trigger fires before it leaves for the graveyard."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        out = D.run(state, ["controls", "creature", "power"])
        creatures = {c for (c,) in out["creature"]}
        power = {c: int(v) for (c, v) in out["power"]}
        mine = sorted(c for (pp, c) in out["controls"] if pp == p and c in creatures)
        mine.sort(key=lambda c: (power.get(c, 0), c))     # weakest first, deterministic tie-break
        for c in mine[:n]:
            D._sacrifice(state, c)                         # fires 'when sacrificed', then -> graveyard


@applier("may_pay")
def apply_may_pay(D, state, a, n, tgt, src, ctrl):
    """§118 a recurring OPTIONAL payment 'you may pay {n}. If you do, untap this' (Mana Vault). The driver
    decides through the _choose seam — DEFAULT is NOT to pay (the common line: leave Mana Vault tapped and
    take the draw-step ping), so a policy/search can opt in. Only pays if affordable; on payment, untaps the
    source (the 'untap_self' follow-up)."""
    D._refresh_mana_pool(state, ctrl)
    avail = next((m for (q, m) in state.get("mana_available", set()) if q == ctrl), 0)
    if avail < int(n):
        return                                                # can't afford the optional cost -> can't pay
    if not D._choose(state, "may_pay", (False, True), False):
        return                                                # default: decline (leave it tapped)
    D._spend_ability_mana(state, ctrl, int(n))
    if str(tgt) == "untap_self":
        state.get("tapped", set()).discard((src,))
        print(f"    {a}: {ctrl} pays {{{n}}} and untaps {src}")


@applier("pact_delayed")
def apply_pact_delayed(D, state, a, n, tgt, src, ctrl):
    """§603.7c schedule a Pact's DELAYED upkeep cost: 'at the beginning of your next upkeep, pay <n generic>;
    if you don't, you lose'. Recorded as (controller, cost, turn_scheduled) in _delayed_upkeep; the turn loop
    (driver._resolve_delayed_upkeep) charges it at the controller's NEXT upkeep or makes them lose. n = the
    cost's mana value. The trigger outlives the spell (which is in the graveyard), so it lives in driver state."""
    state.setdefault("_delayed_upkeep", set()).add((ctrl, int(n), state.get("_turn", 0)))
    print(f"    {a}: {ctrl} must pay {{{n}}} at their next upkeep (Pact) or lose the game")


@applier("dyn_counter_draw")
def apply_dyn_counter_draw(D, state, a, n, tgt, src, ctrl):
    """§122 'put a <kind> counter on this, then draw a card for each <kind> counter on this' (The One Ring):
    add one counter to the source, then draw mult × the LIVE counter count (so the just-added one counts)."""
    kind = str(tgt)
    D._bump_counter(state, src, kind, 1)
    count = next((c for (o, k, c) in state.get("counter", set()) if o == src and k == kind), 0)
    draws = int(n) * int(count)
    print(f"    {a}: {ctrl} puts a {kind} counter on {src} ({count}) and draws {draws}")
    for _ in range(draws):
        D._draw(state, ctrl)


@encoder("flip_coin")
def encode_flip_coin(verb, amt, tgt, extra):
    return ("flip_coin", 0, "-")                              # a standalone 'flip a coin' (Tavern Scoundrel)


@applier("flip_coin")
def apply_flip_coin(D, state, a, n, tgt, src, ctrl):
    """§705 a standalone 'flip a coin' (Tavern Scoundrel's activated ability). On a WIN, fire the controller's
    'whenever you win a coin flip' triggers via the won_flip window (the flip routes through the chance seam)."""
    won = D._flip_coin(state, f"flip:{a}") == "heads"
    print(f"    {a}: {ctrl} flips a coin and {'WINS' if won else 'loses'} the flip")
    if won:
        state["won_flip"] = {(ctrl,)}
        D._apply_effects(state, D.run(state, ["pending"])["pending"])
        state["won_flip"] = set()


@applier("coin_flip")
def apply_coin_flip(D, state, a, n, tgt, src, ctrl):
    """§705 flip a coin (50/50 through the chance seam), then apply the matching branch: 'lose:<N>|win:<M>'
    self-damage — on a LOST flip deal N to the controller (Mana Crypt / Ral downside), on a WON flip deal M;
    plus an optional '|transform' rider (§712 Ral, Monsoon Mage: 'if you win, you MAY exile Ral and return him
    transformed') resolved on a WON flip. The flip routes through D._flip_coin so a search/policy can observe
    or fix the outcome; the 'may' transform routes through D._choose (default: take it — a planeswalker)."""
    fields = str(tgt).split("|")
    parts = dict(p.split(":") for p in fields if ":" in p)
    lose_n = int(parts.get("lose", 0)); win_n = int(parts.get("win", 0))
    won = D._flip_coin(state, f"flip:{a}") == "heads"
    print(f"    {a}: {ctrl} flips a coin and {'WINS' if won else 'LOSES'} the flip")
    dmg = win_n if won else lose_n
    if dmg:
        print(f"      {src} deals {dmg} to {ctrl} -> {D._adjust_life(state, ctrl, -dmg)} life")
    if won and "transform" in fields and (src,) in {(o,) for (o,) in state.get("on_battlefield", set())}:
        if D._choose(state, "transform", (True, False), True):  # §712 'you MAY exile ~ and return transformed'
            D._transform(state, src, ctrl)


@applier("sacrifice_self")
def apply_sacrifice_self(D, state, a, n, tgt, src, ctrl):
    """§701.16 'Sacrifice this permanent' — the SOURCE sacrifices itself (City of Traitors on a land drop,
    a self-sac payoff). A no-op if it's already left the battlefield."""
    if (src,) in state.get("on_battlefield", set()):
        D._sacrifice(state, src)                           # fires 'when sacrificed', then -> graveyard
        print(f"    trigger {a}: {ctrl} sacrifices {src}")


# ----- get_energy -----------------------------------------------------------------------------------
@encoder("get_energy")
def encode_energy(verb, amt, tgt, extra):
    n = _int(amt)
    scope = _player_scope(tgt) or ("controller" if str(tgt) in ("-", "None") else None)
    if n is None or n < 0 or scope is None:
        return None
    return ("get_energy", n, scope)


@applier("get_energy")
def apply_energy(D, state, a, n, tgt, src, ctrl):
    """Add n {E} energy counters to the player(s) (§107.16). Energy is a player resource the engine
    doesn't model, so it lives in a driver-side state relation state['energy'] = {(player, total)}."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    energy = state.setdefault("energy", set())
    for p in players:
        cur = next((e for (q, e) in energy if q == p), 0)
        energy.discard((p, cur))
        energy.add((p, cur + n))
        print(f"    trigger {a}: {p} gets {n} energy -> {cur + n}{{E}}")


# ----- win_game / lose_game -------------------------------------------------------------------------
# The effect verb ASSERTS the result into an engine input relation; the engine DERIVES the §704.5 SBA
# (wins_game/loses_game) and the driver ends the game on it. No life-total hack — a real game result.
def _assert_lose(D, state, p):
    """§104.3a — a resolved effect makes p lose: assert eff_lose_game(p); the engine derives loses_game(p)."""
    state.setdefault("eff_lose_game", set()).add((p,))


def _assert_win(D, state, p):
    """§104.2a — a resolved effect makes p win: assert eff_win_game(p); the engine derives wins_game(p)."""
    state.setdefault("eff_win_game", set()).add((p,))


@encoder("lose_game")
def encode_lose(verb, amt, tgt, extra):
    # 'you lose the game' (controller) / 'target player loses' / 'each opponent loses the game'.
    scope = _player_scope(tgt)
    return ("lose_game", 0, scope) if scope else None


@applier("lose_game")
def apply_lose(D, state, a, n, tgt, src, ctrl):
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        print(f"    trigger {a}: {p} loses the game (§104.3a)")
        _assert_lose(D, state, p)


@encoder("win_game")
def encode_win(verb, amt, tgt, extra):
    # 'you win the game' — only the clean controller-scoped form (every corpus instance is tgt='you').
    return ("win_game", 0, "controller") if _player_scope(tgt) == "controller" else None


@applier("win_game")
def apply_win(D, state, a, n, tgt, src, ctrl):
    """§104.2a — the controller wins. Assert eff_win_game(controller); the engine derives wins_game and the
    driver ends the game with that winner (every other player loses)."""
    print(f"    trigger {a}: {ctrl} wins the game (§104.2a)")
    _assert_win(D, state, ctrl)


@applier("win_lib_empty")
def apply_win_lib_empty(D, state, a, n, tgt, src, ctrl):
    """Thassa's Oracle / Jace, Wielder of Mysteries / Laboratory Maniac shape — 'you win the game' GATED on
    'X is greater than or equal to the number of cards in your library' (X = devotion / a count ≥ 0). We
    resolve only the FAITHFUL-CONSERVATIVE slice: the controller wins iff their library is EMPTY, where the
    inequality holds (0 ≤ X) regardless of X. A non-empty library would need the exact X (devotion) we don't
    compute — so we ABSTAIN there (never a wrong win), missing only the rare 'devotion ≥ small library' win.
    This is exactly the Demonic-Consultation / Tainted-Pact combo: empty the library, then win on resolution."""
    lib = sum(1 for (p, _c) in state.get("in_library", set()) if p == ctrl)
    if lib == 0:
        print(f"    trigger {a}: {ctrl}'s library is empty -> {ctrl} wins the game (§104.2a)")
        _assert_win(D, state, ctrl)
    else:
        print(f"    trigger {a}: {ctrl}'s library has {lib} card(s) -> win condition not met (abstain)")


# ----- set_life -------------------------------------------------------------------------------------
@encoder("set_life")
def encode_set_life(verb, amt, tgt, extra):
    n = _int(amt)
    scope = _player_scope(tgt) or ("controller" if str(tgt) in ("-", "None") else None)
    if n is None or scope is None:
        return None
    return ("set_life", n, scope)


@applier("set_life")
def apply_set_life(D, state, a, n, tgt, src, ctrl):
    """A player's life total BECOMES n (§118.5 / 'your life total becomes N')."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        D._set_life(state, p, n)
        print(f"    trigger {a}: {p}'s life becomes {n}")


# ----- dyn_damage -----------------------------------------------------------------------------------
def _dyn_quantity(state, tag: str, ctrl: str) -> int:
    """The live value of a 'deal damage equal to <quantity>' amount for the controller (§120)."""
    if tag == "cards_in_hand":
        return sum(1 for (p, _c) in state.get("in_hand", set()) if p == ctrl)
    return 0                                                  # unknown tag -> 0 (the bridge only emits known tags)


@applier("dyn_damage")
def apply_dyn_damage(D, state, a, n, tgt, src, ctrl):
    """§120 deal damage EQUAL TO a game quantity (Roaring Furnace: cards in your hand) to a target the
    driver picks for the damage class. The amount is computed against live state at resolution, then routed
    through the shared damage resolver (lethality on a creature, life loss on a player)."""
    qty_tag, _, dk = str(tgt).partition("|")
    amount = _dyn_quantity(state, qty_tag, ctrl)
    print(f"    trigger {a}: {ctrl} deals {amount} damage (= {qty_tag.replace('_', ' ')})")
    D._apply_damage(state, a, amount, dk, ctrl)


@applier("discard_draw")
def apply_discard_draw(D, state, a, n, tgt, src, ctrl):
    """§700.2 'each player may discard their hand and draw N cards' (Will of the Jeskai mode1). Per player it
    is a single MAY choice (discard the WHOLE hand, then draw N — atomic). `tgt` = 'scope|may?'. The greedy
    default: the controller takes the new hand; each opponent declines (a legal 'may' decline). The _choose
    seam exposes every player's choice so a policy/search can opt them in/out."""
    scope, _, flag = str(tgt).partition("|")
    may = (flag == "may")
    players = sorted(p for (p,) in state.get("is_player", set())) if scope == "each_player" else [ctrl]
    for p in players:
        if may and not D._choose(state, "discard_draw", (False, True), p == ctrl):
            continue                                          # this player declines (default: only the controller does it)
        hand = [c for (pp, c) in list(state.get("in_hand", set())) if pp == p]
        for c in hand:
            state["in_hand"].discard((p, c))
            state.setdefault("graveyard", set()).add((c,))
        for _ in range(int(n)):
            D._draw(state, p)
        print(f"    {a}: {p} discards their hand ({len(hand)}) and draws {n}")

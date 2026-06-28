"""mtg.society_of_control — a hand-built MTG CONTROL bot, the iteration surface for control heuristics.

`SocietyOfControlPlayer` STARTS as a copy of `DeleuzePlayer` (mtg.deleuze) — which is itself a copy of
`HeuristicPlayer`: develop, attack with intent, block to matter, curve out creatures/permanents, throw a
player-targeting spell at the face. From there it's the canvas to grow CONTROL play onto — trade resources,
answer threats with removal, draw cards, hold up reactive instants, stabilise then win — independently of the
aggro-leaning deleuze. (The name nods to Deleuze's *Postscript on the Societies of Control*, the sequel to the
player it forks.) Everything lives here in one file so the whole strategy is visible and editable: `choose_move`
declares the strategy as one prioritized list of scored preferences, and the `*_choice` methods (over the leaf
eval `_value`) are the metrics. `prioritize` picks the max-scoring move in the first non-empty category, so the
*order of the arguments is the strategy* and the per-move scorers are the *knobs*.

    from mtg import benchmark
    from mtg.society_of_control import SocietyOfControlPlayer
    benchmark(SocietyOfControlPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import env

from .game import Game
from .models import Move, PriorityOption as Do
from .players import Player


class SocietyOfControlPlayer(Player):
    """A hand-built MTG CONTROL heuristic, started as a copy of `DeleuzePlayer`: develop, attack with intent,
    block to matter, curve out. This is the canvas to evolve TOWARD control — removal, card advantage, reactive
    instants, stabilise-then-win. `choose_move` declares the strategy as one prioritized list of scored
    preferences; the `*_choice` methods are the metrics."""

    name = "society_of_control"

    # choose_move scores land plays (Do.LANDS / land_choice), so it needs the §305 land drop surfaced as a
    # move — the harness reads this and builds the Game with explicit_lands (else lands auto-develop and the
    # land logic never fires).
    wants_explicit_lands = True

    # leaf board-eval weights (used by _value; dial to tune the eval)
    W_LIFE_DIFF = 0.05
    W_AGGRO = 0.30          # push opponent toward 0
    W_BOARD_POWER = 0.30
    W_PRESENCE = 0.10
    W_CARDS = 0.08

    # combat scorer weights (dial to tune aggression / risk tolerance)
    LETHAL = 1000.0         # overwhelming bonus (attack) / penalty (block) for a lethal swing
    W_DAMAGE = 2.0          # value per point of damage an attack lands
    W_CRACKBACK = 1.5       # penalty weight on a lethal-looking crackback
    W_TRADE = 1.5           # value weight on winning / losing a creature in combat

    # curve-out: among castable creatures, deploy the CHEAPER ones first. W_CURVE (>> the develop_choice spread)
    # makes mana value the primary sort and the board metric the tiebreak; _CURVE_BASE keeps every castable
    # creature above the floor=0.0 gate so it still gets cast (a real mana value never exceeds _CURVE_BASE).
    W_CURVE = 1.0
    _CURVE_BASE = 20.0

    # burn_choice base: a lethal creature-removal cast scores _BURN_BASE + the dead creature's power, so the
    # biggest threat is answered first; >0 clears the floor=0.0 gate (it only fires on an actual kill).
    _BURN_BASE = 1.0

    # forced-win gate slack: extra headroom on the cheap reach estimate so we never skip the (expensive) win scan
    # when a real kill is on the table — only when nobody is plausibly in range.
    _WIN_SLACK = 2

    def choose_move(self, game) -> Move | None:
        self.bind(game)                          # so self.creatures / self.opponent / self.life are live here
        win = self.force_win(game)               # TAKE OVER: if a win is forceable THIS turn, close the game —
        if win is not None:                      # a lethal line beats any positional heuristic (on our turn OR,
            return win                           # at instant speed, on the opponent's: the commander 'mousetrap')
        return game.prioritize(
            Do.LANDS.prefer(self.land_choice),                  # play a land (non-basics first),
            Do.RESOLVE_TRIGGER.prefer(self.resolve_choice, floor=0.0),  # then aim a player-targeting spell at their face,
            Do.SPELLS.prefer(self.burn_choice, floor=0.0),      # then KILL an opp creature with creature-target damage,
            Do.SPELLS.prefer(self.mana_rock_choice, floor=0.0),  # then RAMP — mana rocks/dorks before other creatures,
            Do.SPELLS.prefer(self.creature_choice, floor=0.0),  # then CREATURES — develop the board before other spells,
            Do.SPELLS.prefer(self.permanent_choice, floor=0.0),  # then deploy other PERMANENTS (artifact/enchantment/PW),
            Do.SPELLS.prefer(self.develop_choice, floor=0.0),   # else the best remaining spell (instant/sorcery) if it beats passing,
            Do.ABILITIES.prefer(self.develop_choice, floor=0.0),  # else the best ability, same gate,
            Do.ATTACKS.prefer(self.attack_choice),              # else the best attack declaration,
            Do.BLOCKS.prefer(self.block_choice),                # else the best block assignment,
            Do.SKIP,                                            # else pass.
        )

    # ---- forced-win take-over (close the game when lethal is available) --------------------------

    def is_win_forceable(self, game) -> bool:
        """True if a WIN is available THIS turn — a move whose resolution drops an opponent to 0 with us still
        alive, on OUR turn or, at instant speed, on the OPPONENT's (the commander 'mousetrap': sink unspent mana
        into its {X}: deal X ability for lethal). Just detects that a lethal move EXISTS now; `force_win` returns
        the move and `choose_move` takes it — move-by-move, re-checked after each step (so we never have to
        predict the opponent's replies, only react to the board in front of us)."""
        return self.force_win(game) is not None

    def force_win(self, game) -> "Move | None":
        """The next Move of a lethal line available this turn, or None. TAKE OVER from the positional heuristic to
        close the game. v1 = the first legal CAST/ACTIVATE/COMBAT move whose 1-ply resolution wins (lethal burn, a
        lethal X 'mousetrap' activation — the engine auto-pays the max affordable X, so a killing activation reads
        as a win here). Lands/pass can't win in one step and are skipped (cheap). A multi-card kill lands move-by-
        move: each decision we re-detect, so the line is taken as it becomes lethal."""
        # cheap gate: only bother simulating when the opponent is actually within reach this turn (avoids a full
        # env.step scan every decision when there's obviously no kill).
        if not self._lethal_plausible(game):
            return None
        for m in game.legal_moves:
            if getattr(m, "kind", None) in (None, "pass", "skip", "play", "land"):
                continue                                       # these never win on their own resolution
            if self._wins_now(game, m):
                return m
        return None

    def _lethal_plausible(self, game) -> bool:
        """Cheap upper-bound gate for the win scan: is an opponent low enough that THIS turn's burst could plausibly
        kill them? Bound = our untapped board power (a swing) + available mana (a proxy for X-burn / the mousetrap)
        + a small slack. Conservative on the high side so we never gate out a real kill, but skips the expensive
        simulation when nobody is in range."""
        st = game.state
        mana = max((m for (p, m) in st.get("mana_available", set()) if p == self.seat), default=0)
        swing = sum(c.power for c in self.creatures if not c.tapped)
        reach = mana + swing + self._WIN_SLACK
        return any(v <= reach for p, v in game.life().items() if p != self.seat)

    def _wins_now(self, game, move) -> bool:
        """Does resolving `move` win immediately — every opponent to 0 (or the game over in our favour) with us
        still alive? Simulated 1-ply via env.step (which auto-advances through resolution); False if the step can't
        resolve (e.g. it needs a follow-up choice the lookahead can't supply)."""
        try:
            child = Game.from_state(env.step(game.state, move.raw))
        except Exception:
            return False
        if child.is_game_over():
            return child.winner() == self.seat
        life = child.life()
        return life.get(self.seat, 1) > 0 and any(v <= 0 for p, v in life.items() if p != self.seat)

    def choose_x(self, game, *, lethal: int | None = None, affordable: int | None = None) -> int:
        """PLACEHOLDER for the 'choose value for X' decision (the commander mousetrap's {X}: deal X). Pick X to be
        exactly lethal when we know it, else the max affordable. NOTE: the engine ALREADY greedily pays the max
        affordable X by default (driver._choose 'x_value'), so a self-play kill works without this — it's the hook
        for once we drive X explicitly. TODO(inthearena): the live 'choose value for X' slider isn't a parsed GRE
        Req yet, so the bridge can't enact it — next step is to STOP the bot on that screen, screenshot it, model
        its Req, and wire an executor (set the slider to `choose_x`'s value, confirm)."""
        if lethal is not None and (affordable is None or lethal <= affordable):
            return lethal
        return affordable or 0

    # ---- choices (score ONE move; game.prioritize picks the max in each category) ----------------

    def land_choice(self, game, move) -> float:
        """Prefer NON-BASIC lands first: basics are the most fungible (any deck can fetch/replay them), so
        spend the scarcer, ability-bearing non-basics first and keep basics in reserve. (Only relevant under
        explicit_lands — in the default mode lands auto-develop and don't surface as moves.)"""
        return 0.0 if move.card.is_basic else 1.0

    def _creature_damage(self, game, card_id) -> int | None:
        """The most damage a CREATURE-TARGETING (not face) `deal_damage` effect of `card_id` deals, or None if it
        has no such effect (or a variable amount). Read from `card_effect` (slug-keyed: verb/amount/scope). A
        scope that can hit a PLAYER ('any_target', 'target_creature_or_player') is FACE burn — handled by the
        resolve line — so it's excluded here; only 'target_creature' / '…creature_or_planeswalker' / the like
        count. `card_effect` is empty in contexts that don't load card rules (the live bridge today), so this
        returns None there and `burn_choice` stays cleanly inert until the card's coverage loads."""
        slug = next((s for (i, s) in game.state.get("instance_of", set()) if i == card_id), None)
        if slug is None:
            return None
        best = None
        for row in game.state.get("card_effect", set()):
            c, _aid, _seq, verb, amount, scope = row[0], row[1], row[2], row[3], row[4], row[5]
            if c != slug or verb != "deal_damage" or "creature" not in scope or "player" in scope or "any" in scope:
                continue
            if str(amount).isdigit():
                best = max(best or 0, int(amount))
        return best

    def burn_choice(self, game, move) -> float:
        """Cast a CREATURE-TARGETING damage spell (strictly target creature, NOT 'any target') ONLY when it WOULD
        KILL an opponent creature — removal that answers a threat, never fired for its own sake. A damage spell
        picks its target at RESOLUTION, so this cast-time choice fires when SOME opponent creature is within the
        spell's damage (its toughness <= the damage dealt), scoring by the biggest such threat removed; -inf
        otherwise, so the floor=0.0 gate skips it (no lethal target -> hold it; a face-burn 'any target' spell ->
        handled by the resolve line). Lethality is `damage >= toughness` (ignores marked damage / deathtouch /
        indestructible — a heuristic). Active wherever card rules are loaded (engine self-play now; the live
        bridge once the card's coverage loads). (Picking WHICH creature at resolution is the resolve-line job.)

        COMMANDER RESERVE: if the spell could kill the opponent's COMMANDER, hold it unless a SECOND commander-
        killing burn card is in hand — a commander recasts from the command zone, so we always keep one answer
        in reserve for its next appearance rather than spending our last on it now."""
        if getattr(move, "kind", None) != "cast" or move.card is None:
            return float("-inf")
        dmg = self._creature_damage(game, move.card.id)
        if dmg is None:
            return float("-inf")                       # not a creature-target damage spell (or it's face burn)
        killable = [c.power for o in self.opponents for c in o.creatures if c.toughness <= dmg]
        if not killable:
            return float("-inf")                       # no opponent creature it would kill -> don't fire it
        # COMMANDER RESERVE: if this spell could kill the opponent's commander, HOLD it unless we have ANOTHER
        # burn card in hand that could also kill it — a commander returns to the command zone and gets recast, so
        # we always keep at least one answer for its next appearance instead of spending our last one now.
        cmd = self._opp_commander(game)
        if cmd is not None and cmd.toughness <= dmg:
            backups = self._commander_answers_in_hand(game, cmd.toughness) - {move.card.id}
            if not backups:
                return float("-inf")                   # our only commander-answer -> reserve it
        return self._BURN_BASE + max(killable)         # cast it; weight by the BIGGEST threat it can remove

    def _opp_commander(self, game):
        """The opponent's COMMANDER as a battlefield creature (is_commander + opponent-controlled), or None. A
        commander stays a commander on the board (the bridge tracks its card id across the cast from the command
        zone), so this catches the case where we could burn it down."""
        cmd = game.state.get("is_commander", set())
        return next((c for o in self.opponents for c in o.creatures if (c.id,) in cmd), None)

    def _commander_answers_in_hand(self, game, toughness) -> set:
        """Instance ids of OUR hand cards that are creature-target burn able to kill a creature of `toughness` —
        the pool of commander answers we could hold in reserve."""
        return {inst for (seat, inst) in game.state.get("in_hand", set())
                if seat == self.seat and (d := self._creature_damage(game, inst)) is not None and d >= toughness}

    def _taps_for_mana(self, game, card_id) -> bool:
        """True if `card_id` is a MANA ROCK / DORK — has a repeatable {T} mana ability (an artifact OR a creature
        that taps for mana). A one-shot 'sacrifice for mana' doesn't count (not a standing ramp source). Read from
        `mana_ability` (slug-keyed: cost string), so it's empty — and this False — without card rules loaded."""
        slug = next((s for (i, s) in game.state.get("instance_of", set()) if i == card_id), None)
        if slug is None:
            return False
        return any(s == slug and "{T}" in str(c) and "sacrifice" not in str(c).lower()
                   for (s, c) in game.state.get("mana_ability", set()))

    def mana_rock_choice(self, game, move) -> float:
        """Deploy MANA ROCKS / DORKS (artifacts or creatures that {T} for mana) BEFORE other creatures — ramp
        first so the bigger threats and the commander come online sooner. Curves out cheaper-first (like
        creature_choice); -inf for anything that isn't a tap-for-mana permanent, so the floor=0.0 gate skips it
        and a mana dork (a creature) is cast HERE rather than via the later creature line."""
        card = move.card
        if getattr(move, "kind", None) != "cast" or card is None or not self._taps_for_mana(game, card.id):
            return float("-inf")
        mv = self._mana_value(game, card.id)
        return (self._CURVE_BASE - self.W_CURVE * mv) + self._develop_tiebreak(game, move)

    def creature_choice(self, game, move) -> float:
        """Score a CREATURE spell; a NON-creature spell scores -inf so it never wins this category. Placed before
        the general SPELLS line in `choose_move`, this casts creatures BEFORE other spell types. Among creatures
        it CURVES OUT — prefers the CHEAPER mana value first (a 1-drop before a 4-drop), with the 1-ply board
        metric (`develop_choice`) as the tiebreak between equal-cost creatures."""
        card = move.card
        if card is None or not card.has_type("creature"):
            return float("-inf")
        mv = self._mana_value(game, card.id)
        return (self._CURVE_BASE - self.W_CURVE * mv) + self._develop_tiebreak(game, move)

    # the non-creature PERMANENT types this bot deploys (creatures go through creature_choice; instants/sorceries
    # are held). Lands are handled by Do.LANDS.
    _PERMANENT_TYPES = ("artifact", "enchantment", "planeswalker", "battle")

    def permanent_choice(self, game, move) -> float:
        """Score a NON-creature PERMANENT (artifact / enchantment / planeswalker / battle); a creature (handled by
        the earlier creature line) or a non-permanent (instant / sorcery) scores -inf. Placed after the creature
        line, this DEPLOYS the board's other permanents even though `_value` can't score their effect (the engine
        doesn't model uncovered card text) — `_CURVE_BASE` lifts them above the floor=0.0 gate that otherwise
        drops them (casting a non-creature is a small _value LOSS — a spent card, no board power). Curves out
        cheaper-first like creatures. Instants/sorceries are deliberately NOT deployed here — they stay in hand
        for the develop line, which only fires them if they actually beat passing."""
        card = move.card
        if card is None or card.has_type("creature") or not any(card.has_type(t) for t in self._PERMANENT_TYPES):
            return float("-inf")
        mv = self._mana_value(game, card.id)
        return (self._CURVE_BASE - self.W_CURVE * mv) + self._develop_tiebreak(game, move)

    def _develop_tiebreak(self, game, move) -> float:
        """`develop_choice` used as the curve TIEBREAK — but a FAILED 1-ply lookahead (env.step raised, e.g. an
        uncovered/complex card resolution on a board with triggers like lifegain or Deafening Silence) returns
        -inf, and -inf would veto a deploy the curve already decided. Clamp it to 0 so the creature/permanent
        still gets cast, just without the board-value ordering refinement (the original heuristic's value-gated
        SPELLS line keeps the -inf there, where 'only act if it beats passing' is the intended behaviour)."""
        dv = self.develop_choice(game, move)
        return dv if dv != float("-inf") else 0.0

    def _mana_value(self, game, card_id) -> int:
        """The mana value (CMC) of `card_id` from the engine state — generic `mana_cost` plus the coloured
        `mana_pip` counts. (In the inthearena bridge `mana_cost` is fed as the total CMC from MTGA and there's no
        `mana_pip`, so this still sums to the right value; 0 when the cost is unknown.)"""
        st = game.state
        return (sum(n for (s, n) in st.get("mana_cost", set()) if s == card_id)
                + sum(n for (s, _c, n) in st.get("mana_pip", set()) if s == card_id))

    def resolve_choice(self, game, move) -> float:
        """Resolve a TARGETED effect at the OPPONENT (Do.RESOLVE_TRIGGER). The engine enumerates one cast/
        activate variant per legal target, so this scores the variant whose target is an opponent PLAYER at
        1.0 and every other variant at 0.0. Under the `floor=0.0` in choose_move that means only a player-
        targeting play (burn / 'target player') fires here — aimed at their face — while creature-targeting
        and untargeted plays score 0 and fall through to normal development. The SAME 'a player target -> the
        opponent' rule drives the inthearena bridge's MTGA SelectTargets pick (see engine_policy)."""
        target = (move.choices or {}).get("target")
        return 1.0 if target in {o.seat for o in self.opponents} else 0.0

    def attack_choice(self, game, move) -> float:
        """Value of declaring `move`'s attackers: damage that lands under a worst-case block (they block our
        biggest, the rest connect) plus an outright lethal swing, minus a lethal-looking crackback from the
        untapped creatures we'd leave home. Empty attack scores -1 (passing up an attack is rarely right)."""
        attackers = move.attackers
        if not attackers:
            return -1.0
        opp_blockers = self.opponent.blockers                          # their untapped creatures
        n_blockers = len(opp_blockers)
        opp_swing = sum(c.power for c in opp_blockers)                  # what they could hit back with
        my_life, opp_life = self.life, self.opponent.life
        powers = sorted((c.power for c in self.creatures if c.id in attackers), reverse=True)
        unblocked = sum(powers[n_blockers:]) if n_blockers < len(powers) else 0
        landed = min(unblocked, opp_life)
        staying = [c for c in self.creatures if c.id not in attackers and not c.tapped]
        my_def = my_life + sum(c.toughness for c in staying)
        risk = max(0, opp_swing - my_def) * self.W_CRACKBACK
        return (self.LETHAL if unblocked >= opp_life else 0.0) + self.W_DAMAGE * landed - risk

    def block_choice(self, game, move) -> float:
        """Value of `move`'s block assignment: damage prevented and trading up (their_loss), minus losing our
        own creatures (my_loss), minus a hard penalty for leaving a lethal amount unblocked. The attacking
        creatures come from the engine state (so an unblockable attacker's damage still counts)."""
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for (a, _d) in game.state.get("attacks", set())}
        blocks = move.blocks
        blocked = {a for (_b, a) in blocks}
        prevented = their_loss = my_loss = 0.0
        for (b, a) in blocks:
            ac, bc = game.card(a), game.card(b)
            prevented += ac.power
            if bc.power >= ac.toughness:
                their_loss += self._creature_value(ac)
            if ac.power >= bc.toughness:
                my_loss += self._creature_value(bc)
        unblocked = sum(game.card(a).power for a in attackers if a not in blocked)
        lethal_pen = self.LETHAL if unblocked >= my_life else 0.0
        return prevented + self.W_TRADE * their_loss - self.W_TRADE * my_loss - lethal_pen

    def develop_choice(self, game, move) -> float:
        """How much a non-combat play `move` IMPROVES the board: _value(after the play) - _value(now), via a
        1-ply lookahead. A value-negative play scores < 0, so the `floor=0.0` in choose_move skips it (and
        the chain falls through to passing) — the old 'only act if it beats sitting still' gate."""
        try:
            child = Game.from_state(env.step(game.state, move.raw))    # env.step normalises the Move to .raw
        except Exception:
            return float("-inf")
        return self._value(child, self.seat) - self._value(game, self.seat)

    @staticmethod
    def _creature_value(c) -> float:                                   # a rough creature worth for a trade
        return c.power + c.toughness + 1.0

    # ---- leaf board eval -------------------------------------------------------------------------

    def _value(self, game, seat: str) -> float:
        """Aggression-tilted board eval in [-1, 1] from `seat`'s view. Reads the board through the bound seat
        views (`self.creatures` / `self.opponent.creatures` — typed `Permanent`s with `.power`), so the bind
        is pointed at `game` for the read and restored afterwards (`game` may be a hypothetical child)."""
        if game.is_game_over():
            w = game.winner()
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        life = game.life()
        if seat not in life:
            return 0.0
        my_life = life[seat]
        opp_life = min((v for p, v in life.items() if p != seat), default=20)
        with self.bound(game, seat):                           # point self.creatures/.opponent at `game`, restore after
            my_creatures, opp_creatures = self.creatures, self.opponent.creatures
        my_pow = sum(c.power for c in my_creatures)
        opp_pow = sum(c.power for c in opp_creatures)
        hands = game.hand_count()
        my_hand = hands.get(seat, 0)
        opp_hand = max((v for p, v in hands.items() if p != seat), default=0)
        v = (self.W_LIFE_DIFF * (my_life - opp_life) / 20.0
             + self.W_AGGRO * (20 - opp_life) / 20.0
             + self.W_BOARD_POWER * (my_pow - opp_pow) / 10.0
             + self.W_PRESENCE * (len(my_creatures) - len(opp_creatures)) / 6.0
             + self.W_CARDS * (my_hand - opp_hand) / 5.0)
        if my_life <= 5:
            v -= 0.25
        return max(-0.99, min(0.99, v))

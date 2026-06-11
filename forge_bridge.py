"""forge_bridge.py — let our Python agent play AS A PLAYER inside a Forge game (vs the Forge AI).

WHY this shape. Forge (https://github.com/Card-Forge/forge) is a complete, AUTHORITATIVE Java rules
engine. Two rules engines can't co-simulate one game, so we don't try: **Forge runs the match and owns
the state; our Python agent is one seat.** Forge asks the seat to make each decision (mulligan, which
spell to play, targets, blocks, …) and our agent answers. That request/answer shape is exactly the
`_choose` / `legal_actions` seam the shim already exposes — so this module is a thin TRANSLATOR between
Forge's `PlayerController` decision calls and our policy, not a second engine.

  Forge (Java)  --decide-->  forge_bridge.ForgePlayer  --(state,key,options,default)-->  policy
                <--choice--                            <--chosen option--

TRANSPORT is line-delimited JSON, over a TCP socket (`serve`/`connect`) or stdio pipe (`run_stdio`). The
decision LOGIC is `ForgePlayer.handle(msg) -> reply`, a pure function of (message, policy, last observation)
— so it's fully testable in-process against a mock Forge (see test_forge_bridge.py) with no socket/JVM.

PROTOCOL (Forge connector -> us):
  {"type":"hello","you":<seatId>,"players":[...],"variant":...}      -> we reply {"type":"ready","name":...}
  {"type":"observe","state":{...}}                                    push a state snapshot (for the policy)
  {"type":"decide","id":N,"kind":K,"options":<kind-specific>,"default":<opt?>,"context":{...}}
  {"type":"result","winner":<seatId|null>} / {"type":"bye"}
us -> Forge:
  {"type":"ready",...} / {"type":"choice","id":N,"value":<kind-specific>}

DECISION KINDS (K) mirror Forge's PlayerController methods; options/value encodings in `_decide`:
  mulligan      <- mulliganKeepHand            value: bool (keep)
  action        <- getAbilityToPlay            options:[{id,label,kind}] incl a pass; value: chosen id-dict
  target        <- chooseTargetsFor            options:[{id,label}]      value: chosen id-dict (single)
  mode          <- chooseModeForAbility        options:[{id,label}]      value: chosen id-dict
  number        <- chooseNumber (X)            options:{min,max}         value: int
  confirm       <- confirmAction (a 'may')     options:[true,false]      value: bool
  choose        <- chooseSingleEntityForEffect options:[{id,label}]      value: chosen id-dict
  discard       <- chooseCardsToDiscard        options:{cards:[…],n:k}   value: [chosen ids]
  attackers     <- declareAttackers            options:{attackers:[…],defenders:[…]} value:[[atk,def],…]
  blockers      <- declareBlockers             options:{pairs:[[blk,atk],…]}         value:[[blk,atk],…]

INTEGRATION (the Forge/Java side — small, not in this repo): add a `PlayerController` that forwards each
decision method to this bridge. e.g. a `NetworkBotController extends PlayerControllerAi` whose
`mulliganKeepHand`, `getAbilityToPlay`, `chooseTargetsFor`, `declareAttackers/Blockers`, `chooseNumber`,
`confirmAction`, `chooseSingleEntityForEffect` each serialize a `decide` message, block on the socket for
the `choice`, and return it as the Forge object (look the id up in the live game). Run forge_bridge.serve()
first, point the Java connector at host:port, seat it as the AI's opponent, and the match plays out.
(Falling back to PlayerControllerAi for any decision kind this agent doesn't answer keeps a game legal.)

POLICY: any `(state, key, options, default) -> choice` (the shim's _choose signature) drives the bot —
game.random_policy / greedy_policy work as-is. The `state` passed is the latest Forge observation (a dict),
not our datalog state: Forge is authoritative, so the bot picks among the options FORGE deems legal. A
stronger, engine-backed policy (reconstruct our facts from the observation, run env lookahead) plugs in at
the same seam — `engine_policy` marks that hook.
"""

from __future__ import annotations

import json
import random
import socket

# Forge PlayerController decision method -> our _choose seam key (so a policy keyed on the shim's own
# decision vocabulary drives the Forge bot unchanged).
_KIND_KEY = {
    "mulligan": "mulligan", "action": "action", "target": "target", "mode": "mode",
    "number": "number", "confirm": "confirm", "choose": "choose", "discard": "discard",
    "attackers": "attackers", "blockers": "blocks",
}


def greedy_policy(state, key, options, default):
    """Take Forge's suggested default at every decision (defers to Forge's own AI heuristic for that
    seat's pick) — the simplest legal bot."""
    return default


def random_policy(seed: int = 0):
    """A uniform-random policy over the offered options, with its OWN seeded RNG (reproducible games vs
    the Forge AI). Returns a `(state, key, options, default)` callable."""
    rng = random.Random(seed)

    def pol(state, key, options, default):
        opts = list(options) if options is not None else []
        return rng.choice(opts) if opts else default
    return pol


# ---- engine-backed policy: OUR engine provides and plays the move (Forge owns the state) --------------
# Forge guarantees the offered options are legal; the COMPLETENESS signal is whether OUR rules engine can
# independently MODEL each option (recognize the card/ability) and ENDORSE it (derive it as legal/playable
# in our own model). A modeled+endorsed move means our engine understands that game situation; an option we
# can't model or endorse is a coverage GAP we record. The bot always returns a VALID Forge option — when our
# engine endorses one it plays that; otherwise it falls back (and logs the gap). This is the "Stockfish for
# Magic": the shim's own engine choosing the move in a Forge-refereed game.

# Forge zone -> (our relation, is it player-scoped?). battlefield/graveyard/exile are arity-1 (card,);
# hand/library are arity-2 (player, card) — matching the engine's .decl for each.
_OBS_ZONE = {"battlefield": ("on_battlefield", False), "graveyard": ("graveyard", False),
             "exile": ("exile", False), "hand": ("in_hand", True), "library": ("in_library", True)}


def reconstruct(obs: dict, seat: str):
    """Build OUR driver state from a Forge observation snapshot (players/life/step + per-zone cards, each
    {id, name, controller, tapped?, counters?}). Each named card is bridged from cards.dl via its ORACLE
    NAME (bridge.card_facts) under its Forge id, so our engine reasons over the same board Forge shows.
    Returns (state, unmodeled) where `unmodeled` lists (zone, name) cards our interpreter doesn't cover —
    the direct completeness gaps. Cards not in our corpus still get a bare object (so id plumbing works)."""
    import driver
    import bridge_to_engine as bridge
    import sim
    import card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    players = obs.get("players") or [seat]
    life = {p: int(v) for p, v in (obs.get("life") or {}).items()}
    state: dict = {
        "is_player": {(p,) for p in players},
        "active_player": {(obs.get("active", players[0]),)},
        "current_step": {(obs.get("step", "precombat_main"),)},
        "life": {(p, life.get(p, 20)) for p in players},
        "on_battlefield": set(), "in_hand": set(), "graveyard": set(), "exile": set(),
        "in_library": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
        "_land_played": set(),
    }
    unmodeled: list = []
    for zone, cards in (obs.get("zones") or {}).items():
        spec = _OBS_ZONE.get(zone)
        if spec is None:
            continue
        rel, player_scoped = spec
        for card in cards:
            cid, name = str(card["id"]), card["name"]
            ctrl = card.get("controller", seat)
            state.setdefault(rel, set()).add((ctrl, cid) if player_scoped else (cid,))
            state.setdefault("printed_control", set()).add((ctrl, cid))
            if name not in corpus:                           # a card our interpreter doesn't model -> gap
                unmodeled.append((zone, name))
                continue
            facts, _ = bridge.card_facts(name, ctrl, cid, db, corpus)
            for r, rows in facts.items():
                state.setdefault(r, set()).update(rows)
            c = corpus[name]
            for t in c.get("types") or []:                   # castable/playable from hand
                state.setdefault("spell_type", set()).add((cid, t.lower()))
            state.setdefault("mana_cost", set()).add((cid, bridge._mana_value(c.get("manaCost"))))
            bridge._register_colored(state, cid, c)
            if card.get("tapped"):
                state["tapped"].add((cid,))
            for kind, k in (card.get("counters") or {}).items():
                driver._bump_counter(state, cid, kind, int(k))
    bridge._materialize_printed(state)
    return state, unmodeled


class EnginePolicy:
    """A policy (state, key, options, default)->choice in which OUR datalog engine selects the move. It
    reconstructs the board from the Forge observation, asks the engine what it can model/endorse among the
    offered options, plays an endorsed move when one exists (else falls back), and records coverage stats
    — the running indication of how completely our engine models a real game. `__call__` matches the
    ForgePlayer policy seam, so it drops in wherever random_policy/greedy_policy go."""

    def __init__(self, fallback=greedy_policy):
        self.fallback = fallback
        self.stats = {"decisions": 0, "engine_decided": 0, "offered": 0, "modeled": 0, "endorsed": 0,
                      "unmodeled_cards": set(), "by_kind": {}}

    def __call__(self, obs, key, options, default):
        seat = obs.get("seat")
        handler = getattr(self, f"_pick_{key}", None)
        self.stats["decisions"] += 1
        if handler is None or seat is None:
            return self.fallback(obs, key, options, default)
        try:
            import driver
            state, unmodeled = reconstruct(obs, seat)
            self.stats["unmodeled_cards"].update(n for _z, n in unmodeled)
            choice, modeled, endorsed, offered, used_engine = handler(driver, state, seat, options, default)
        except Exception:
            return self.fallback(obs, key, options, default)
        bk = self.stats["by_kind"].setdefault(key, {"offered": 0, "modeled": 0, "endorsed": 0, "engine": 0})
        bk["offered"] += offered; bk["modeled"] += modeled; bk["endorsed"] += endorsed; bk["engine"] += used_engine
        self.stats["offered"] += offered; self.stats["modeled"] += modeled; self.stats["endorsed"] += endorsed
        self.stats["engine_decided"] += used_engine
        return choice if choice is not None else self.fallback(obs, key, options, default)

    # each _pick_* returns (choice, modeled, endorsed, offered, used_engine)
    def _pick_action(self, driver, state, seat, options, default):
        """Develop mana (play a land) then cast a spell OUR engine derives as castable (can_cast); else pass.
        Completeness check: of the play options Forge offers, how many does our engine recognize (modeled) and
        deem playable (endorsed = a land drop our engine owns, or a spell can_cast endorses)."""
        driver._refresh_mana_pool(state, seat)               # stock mana from the reconstructed lands
        state["has_priority"] = {(seat,)}                    # §117 — can_cast is gated on holding priority
        can = {s for (p, s) in driver.run(state, ["can_cast"])["can_cast"] if p == seat}
        ptype = driver.run(state, ["printed_type"])["printed_type"]
        lands_in_hand = {c for (p, c) in state.get("in_hand", set()) if p == seat and (c, "land") in ptype}
        objs = {o for (o,) in state.get("on_battlefield", set())} | {c for (_p, c) in state.get("in_hand", set())}
        spells = [o for o in options if isinstance(o, dict) and o.get("kind") == "spell"]
        modeled = sum(1 for o in spells if str(o["id"]) in objs)
        land_opts = [o for o in spells if str(o["id"]) in lands_in_hand]      # play a land (develop mana)
        cast_opts = [o for o in spells if str(o["id"]) in can]               # cast an affordable spell
        endorsed = land_opts + cast_opts
        # §702.40 storm-aware ordering: a storm spell copies once per spell cast BEFORE it this turn, so cast
        # every OTHER spell first (build the count) and hold the storm payoff until nothing else is castable.
        # Reads the keyword off the reconstructed card identity (card_keyword via instance_of). A purely
        # generic heuristic — the engine still decides WHICH spells to cast; this only orders the payoff last.
        def _is_storm(o):
            return "storm" in driver._spell_keywords(state, str(o["id"]))
        non_storm = [o for o in cast_opts if not _is_storm(o)]
        storm_opts = [o for o in cast_opts if _is_storm(o)]
        choice = (land_opts[0] if land_opts else                             # land > non-storm spell > storm
                  non_storm[0] if non_storm else
                  storm_opts[0] if storm_opts else default)
        return choice, modeled, len(endorsed), len(spells), 1

    def _pick_target(self, driver, state, seat, options, default):
        """Target an entity our engine models as a legal creature target."""
        creatures = {c for (c,) in driver.run(state, ["creature"])["creature"]}
        opts = [o for o in options if isinstance(o, dict)]
        modeled = [o for o in opts if str(o["id"]) in creatures]
        choice = modeled[0] if modeled else (opts[0] if opts else default)
        return choice, len(modeled), len(modeled), len(opts), 1

    def _pick_attackers(self, driver, state, seat, options, default):
        """Among Forge's candidate attack declarations, pick the one whose attackers OUR engine endorses
        (may_attack) and that swings widest — our engine choosing the attack."""
        eligible = {c for (c,) in driver.run(state, ["may_attack"])["may_attack"]}
        best, best_n = default, -1
        offered = sum(len(c) for c in options if c)
        endorsed = 0
        for cand in options:
            ok = [pair for pair in cand if str(pair[0]) in eligible]
            if len(ok) > best_n:
                best, best_n = (cand if len(ok) == len(cand) else ok), len(ok)
            endorsed = max(endorsed, len(ok))
        return best, endorsed, endorsed, max(offered, 1), 1

    def _pick_blocks(self, driver, state, seat, options, default):
        """Among Forge's candidate block assignments, pick one our engine deems legal (no illegal_block)."""
        legal_cand, endorsed = default, 0
        for cand in options:
            probe = driver.clone_state(state)
            probe["blocks"] = {tuple(p) for p in cand}
            bad = driver.run(probe, ["illegal_block"])["illegal_block"]
            if not bad and len(cand) >= endorsed:
                legal_cand, endorsed = cand, len(cand)
        return legal_cand, endorsed, endorsed, max(sum(len(c) for c in options if c), 1), 1

    def coverage(self) -> dict:
        """A completeness report: of the options Forge offered at engine-handled decisions, the fraction our
        engine MODELED (recognized) and ENDORSED (derived as legal/playable), plus the cards it couldn't model."""
        s = self.stats
        frac = lambda a, b: round(a / b, 3) if b else None
        return {
            "decisions": s["decisions"], "engine_decided": s["engine_decided"],
            "options_offered": s["offered"], "modeled": s["modeled"], "endorsed": s["endorsed"],
            "modeled_frac": frac(s["modeled"], s["offered"]), "endorsed_frac": frac(s["endorsed"], s["offered"]),
            "unmodeled_cards": sorted(s["unmodeled_cards"]), "by_kind": s["by_kind"],
        }


def engine_policy(obs, key, options, default):
    """Module-level convenience: a single shared EnginePolicy instance (so its coverage() accumulates)."""
    return _ENGINE_SINGLETON(obs, key, options, default)


_ENGINE_SINGLETON = EnginePolicy()


class ForgePlayer:
    """A Forge seat driven by a policy. `handle(msg)` is a pure message->reply step (None = no reply);
    the transports below just pump messages through it."""

    def __init__(self, policy=greedy_policy, name: str = "datalog-agent"):
        self.policy = policy
        self.name = name
        self.seat = None
        self.obs: dict = {}                 # latest Forge state snapshot, handed to the policy as `state`
        self.history: list = []             # (kind, value) of every decision made — for tests / replay

    # -- message handling ------------------------------------------------------------------------------
    def handle(self, msg: dict):
        t = msg.get("type")
        if t == "hello":
            self.seat = msg.get("you")
            self.obs = {"seat": self.seat, "players": msg.get("players", []), "variant": msg.get("variant")}
            return {"type": "ready", "name": self.name, "seat": self.seat}
        if t == "observe":
            self.obs = {**self.obs, **(msg.get("state") or {})}
            return None
        if t == "decide":
            return self._decide(msg)
        if t in ("result", "bye", "ping"):
            return {"type": "pong"} if t == "ping" else None
        return {"type": "error", "reason": f"unknown message type {t!r}"}

    def _choose(self, key, choices, default):
        """Route one decision through the policy (the shim seam), validating the pick is legal."""
        choice = self.policy(self.obs, key, choices, default)
        if choices and choice not in choices:                # a stray policy pick -> fall back to legal default
            choice = default if default in choices else choices[0]
        self.history.append((key, choice))
        return choice

    def _decide(self, msg: dict) -> dict:
        kind = msg.get("kind")
        key = _KIND_KEY.get(kind, kind)
        opts = msg.get("options")
        default = msg.get("default")
        value = self._resolve(kind, key, opts, default)
        return {"type": "choice", "id": msg.get("id"), "value": value}

    def _resolve(self, kind, key, opts, default):
        # single-pick kinds: the option list IS the choice space; the chosen option is the value.
        if kind in ("action", "target", "mode", "choose"):
            choices = list(opts or [])
            if not choices:
                return default
            return self._choose(key, choices, default if default in choices else choices[0])
        if kind == "mulligan":
            return bool(self._choose(key, [True, False], True if default is None else bool(default)))
        if kind == "confirm":
            return bool(self._choose(key, [True, False], False if default is None else bool(default)))
        if kind == "number":
            lo, hi = int(opts.get("min", 0)), int(opts.get("max", 0))
            rng = list(range(lo, hi + 1)) or [lo]
            return int(self._choose(key, rng, lo if default is None else int(default)))
        if kind == "discard":
            cards = list((opts or {}).get("cards", []))
            n = int((opts or {}).get("n", 0))
            return self._pick_subset(key, cards, exactly=min(n, len(cards)))
        if kind == "attackers":
            return self._declare_attackers(key, opts or {}, default)
        if kind == "blockers":
            return self._declare_blockers(key, opts or {}, default)
        return default                                       # unknown kind -> Forge's default keeps it legal

    # -- combat / multi-pick (the candidate-set style env uses: offer a few full declarations, pick one) -
    def _pick_subset(self, key, items, exactly=None):
        """Choose a subset of `items` by repeatedly picking one (records each via the seam). `exactly` n
        forces a count (discard n); otherwise the policy may stop early via a None sentinel."""
        chosen, pool = [], list(items)
        while pool and (exactly is None or len(chosen) < exactly):
            opt = self._choose(key, pool + ([None] if exactly is None else []), pool[0])
            if opt is None:
                break
            chosen.append(opt)
            pool.remove(opt)
        return chosen

    def _declare_attackers(self, key, opts, default):
        """Offer candidate attack declarations (none / all-at-first-defender / each singleton) and pick
        one — the same finite candidate-set env.legal_actions uses, but over Forge-provided ids."""
        attackers = list(opts.get("attackers", []))
        defenders = list(opts.get("defenders", [])) or [None]
        d0 = defenders[0]
        cands = [[]]                                         # no attack
        if attackers:
            cands.append([[a, d0] for a in attackers])       # alpha strike at the first defender
            cands += [[[a, d0]] for a in attackers]          # each lone attacker
        dflt = default if default in cands else cands[0]
        return self._choose(key, cands, dflt)

    def _declare_blockers(self, key, opts, default):
        """Offer candidate block assignments (none / greedy one-per-attacker / each single legal block)."""
        pairs = [tuple(p) for p in opts.get("pairs", [])]
        cands = [[]]
        greedy, used_b, used_a = [], set(), set()
        for (b, a) in pairs:
            if b not in used_b and a not in used_a:
                greedy.append([b, a]); used_b.add(b); used_a.add(a)
        if greedy:
            cands.append(greedy)
        for (b, a) in pairs:
            if [[b, a]] not in cands:
                cands.append([[b, a]])
        dflt = default if default in cands else cands[0]
        return self._choose(key, cands, dflt)


# ---- transports ---------------------------------------------------------------------------------------

def _pump(player: ForgePlayer, recv_line, send_line) -> str | None:
    """Drive one Forge connection to completion: read JSON lines, hand each to the player, write any
    reply. Returns the game result's winner seat (or None). Transport-agnostic — `recv_line` yields
    decoded str lines (or None at EOF), `send_line` writes one str line."""
    winner = None
    while True:
        line = recv_line()
        if not line:
            break
        msg = json.loads(line)
        if msg.get("type") == "result":
            winner = msg.get("winner")
        reply = player.handle(msg)
        if reply is not None:
            send_line(json.dumps(reply))
        if msg.get("type") in ("result", "bye"):
            break
    return winner


def serve(player: ForgePlayer, host: str = "127.0.0.1", port: int = 0, once: bool = True):
    """Listen for a Forge connector and play seats until it disconnects. Returns (bound_port, winner).
    The Forge-side connector dials host:port and speaks the line-JSON protocol."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    bound = srv.getsockname()[1]
    srv.listen(1)
    winner = None
    try:
        while True:
            conn, _addr = srv.accept()
            with conn, conn.makefile("r") as r, conn.makefile("w") as w:
                def send(s, _w=w):
                    _w.write(s + "\n"); _w.flush()
                winner = _pump(player, lambda _r=r: _r.readline(), send)
            if once:
                break
    finally:
        srv.close()
    return bound, winner


def connect(player: ForgePlayer, host: str, port: int) -> str | None:
    """Dial a Forge connector that is LISTENING (the inverse of serve). Returns the winner seat."""
    with socket.create_connection((host, port)) as conn, conn.makefile("r") as r, conn.makefile("w") as w:
        def send(s):
            w.write(s + "\n"); w.flush()
        return _pump(player, lambda: r.readline(), send)


def run_stdio(player: ForgePlayer, stdin=None, stdout=None) -> str | None:
    """Speak the protocol over stdin/stdout (a pipe transport for a Forge connector that spawns the bot
    as a subprocess). Returns the winner seat."""
    import sys
    ins = stdin or sys.stdin
    outs = stdout or sys.stdout

    def send(s):
        outs.write(s + "\n"); outs.flush()
    return _pump(player, lambda: ins.readline(), send)


if __name__ == "__main__":
    # Stand up a bot server on an ephemeral port for a Forge connector to dial.
    p = ForgePlayer(policy=random_policy(seed=0), name="datalog-random-bot")
    port, win = serve(p, port=0)
    print(f"datalog Forge bot finished on port {port}; winner seat: {win}")

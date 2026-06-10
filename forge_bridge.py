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


def engine_policy(state, key, options, default):
    """HOOK for an engine-backed bot: reconstruct our datalog facts from the Forge observation (`state`),
    run env lookahead/eval, and map the best move back to one of `options`. Not implemented (faithful
    Forge-state -> our-facts translation is a separate effort); falls back to Forge's default so a game
    stays legal. This is where 'a Python player DRIVING the engine' slots in."""
    return default


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

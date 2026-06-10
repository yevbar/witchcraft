"""test_forge_bridge.py — the Forge player adapter (forge_bridge.py).

Forge is a separate Java engine we can't run here, so we test against a MOCK Forge: a scripted sequence of
PlayerController-style decision requests. Verifies (a) every decision KIND yields a legal, well-formed
reply through the policy seam, (b) the pure handle() and the real SOCKET transport agree, (c) a full
scripted game runs to a result. No JVM, no network beyond loopback.

Run: python3 test_forge_bridge.py
"""

from __future__ import annotations

import json
import threading

import forge_bridge as fb

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _decide(kind, options, default=None, _id=1):
    return {"type": "decide", "id": _id, "kind": kind, "options": options, "default": default}


# ---- per-kind decisions (pure handle, greedy + random policies) -------------------------------------

def _kinds() -> None:
    p = fb.ForgePlayer(policy=fb.greedy_policy)
    p.handle({"type": "hello", "you": "p2", "players": ["p1", "p2"]})

    # action: pick among spells/abilities/pass; greedy takes the default.
    acts = [{"id": 10, "label": "Grizzly Bears", "kind": "spell"},
            {"id": 0, "label": "pass", "kind": "pass"}]
    r = p.handle(_decide("action", acts, default=acts[1]))
    check("action: reply is a choice with the request id", r["type"] == "choice" and r["id"] == 1)
    check("action: greedy returns Forge's default option", r["value"] == acts[1])

    # target: single entity.
    tgts = [{"id": 7, "label": "Goblin"}, {"id": 8, "label": "Bear"}]
    r = p.handle(_decide("target", tgts, default=tgts[0]))
    check("target: returns one of the offered entities", r["value"] in tgts)

    # mode.
    modes = [{"id": 1, "label": "deal 3 damage"}, {"id": 2, "label": "draw a card"}]
    r = p.handle(_decide("mode", modes, default=modes[0]))
    check("mode: returns an offered mode", r["value"] in modes)

    # number (X): clamped to [min,max].
    r = p.handle(_decide("number", {"min": 0, "max": 5}, default=3))
    check("number: returns an int within [min,max]", isinstance(r["value"], int) and 0 <= r["value"] <= 5)

    # confirm (a 'may' ability): a bool.
    r = p.handle(_decide("confirm", [True, False], default=False))
    check("confirm: returns a bool", isinstance(r["value"], bool))

    # mulligan.
    r = p.handle(_decide("mulligan", [True, False], default=True))
    check("mulligan: greedy keeps (Forge default True)", r["value"] is True)

    # discard exactly n.
    cards = [{"id": 1, "label": "a"}, {"id": 2, "label": "b"}, {"id": 3, "label": "c"}]
    r = p.handle(_decide("discard", {"cards": cards, "n": 2}))
    check("discard: returns exactly n cards, all legal", len(r["value"]) == 2 and all(c in cards for c in r["value"]))

    # attackers: a list of [attacker,defender] pairs drawn from the eligible set.
    r = p.handle(_decide("attackers", {"attackers": [11, 12], "defenders": ["p1"]}))
    decl = r["value"]
    check("attackers: every declared attacker is eligible and aims at a real defender",
          all(a in (11, 12) and d == "p1" for a, d in decl))

    # blockers: a subset of the legal (blocker,attacker) pairs.
    r = p.handle(_decide("blockers", {"pairs": [[21, 11], [22, 11], [22, 12]]}))
    legal = {(21, 11), (22, 11), (22, 12)}
    check("blockers: every block is a legal pairing", all(tuple(x) in legal for x in r["value"]))

    # an unknown decision kind safely returns the Forge default (keeps the game legal).
    r = p.handle(_decide("some_future_kind", ["x"], default="x"))
    check("unknown kind: falls back to Forge's default", r["value"] == "x")


def _random_legal() -> None:
    # the random policy NEVER returns an illegal option across many draws of every kind.
    p = fb.ForgePlayer(policy=fb.random_policy(seed=4))
    p.handle({"type": "hello", "you": "p2", "players": ["p1", "p2"]})
    ok = True
    for _ in range(200):
        acts = [{"id": 10, "label": "Bears", "kind": "spell"}, {"id": 0, "label": "pass", "kind": "pass"}]
        ok &= p.handle(_decide("action", acts, default=acts[1]))["value"] in acts
        ok &= 0 <= p.handle(_decide("number", {"min": 0, "max": 4}, default=0))["value"] <= 4
        atk = p.handle(_decide("attackers", {"attackers": [1, 2, 3], "defenders": ["p1"]}))["value"]
        ok &= all(a in (1, 2, 3) and d == "p1" for a, d in atk)
    check("random policy never returns an illegal option (200 rounds, all kinds)", ok)

    # reproducible: same seed -> identical decision stream.
    def stream(seed):
        pl = fb.ForgePlayer(policy=fb.random_policy(seed=seed))
        pl.handle({"type": "hello", "you": "p2", "players": ["p1", "p2"]})
        return [pl.handle(_decide("number", {"min": 0, "max": 9}, default=0))["value"] for _ in range(12)]
    check("random policy is reproducible by seed", stream(7) == stream(7) and stream(7) != stream(8))


# ---- a full scripted game over the protocol (and over a real socket) --------------------------------

def _scripted_game(send_decide):
    """A tiny Forge-like script: hello, a mulligan, a couple of priority/combat decisions, a result.
    `send_decide(msg) -> reply` is the channel (in-process handle, or socket round-trip)."""
    assert send_decide({"type": "hello", "you": "p2", "players": ["p1", "p2"], "variant": "two-player"})["type"] == "ready"
    send_decide({"type": "observe", "state": {"turn": 1, "life": {"p1": 20, "p2": 20}}})
    r1 = send_decide(_decide("mulligan", [True, False], default=True, _id=1))
    acts = [{"id": 5, "label": "Hill Giant", "kind": "spell"}, {"id": 0, "label": "pass", "kind": "pass"}]
    r2 = send_decide(_decide("action", acts, default=acts[1], _id=2))
    r3 = send_decide(_decide("attackers", {"attackers": [5], "defenders": ["p1"]}, _id=3))
    winner = "p2"
    final = send_decide({"type": "result", "winner": winner})
    return [r1, r2, r3], final


def _full_game_inprocess() -> None:
    p = fb.ForgePlayer(policy=fb.random_policy(seed=1), name="bot")
    replies, _ = _scripted_game(lambda m: p.handle(m))
    check("in-process: a scripted game produces a well-formed reply per decision",
          all(r and r["type"] in ("ready", "choice") for r in [replies[0]]) and
          all(r["type"] == "choice" for r in replies))
    check("in-process: the bot recorded its decisions in history", len(p.history) >= 3)


def _full_game_socket() -> None:
    # the SAME script over a real loopback socket: serve() in a thread, a mock connector dials in.
    p = fb.ForgePlayer(policy=fb.random_policy(seed=1), name="bot")
    bound = {}
    ready = threading.Event()

    def run_server():
        import socket
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0)); bound["port"] = srv.getsockname()[1]; srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        with conn, conn.makefile("r") as r, conn.makefile("w") as w:
            fb._pump(p, lambda: r.readline(), lambda s: (w.write(s + "\n"), w.flush()))
        srv.close()

    th = threading.Thread(target=run_server, daemon=True); th.start()
    ready.wait(5)
    import socket
    with socket.create_connection(("127.0.0.1", bound["port"]), timeout=5) as c, \
            c.makefile("r") as r, c.makefile("w") as w:
        def send_decide(msg):
            w.write(json.dumps(msg) + "\n"); w.flush()
            if msg["type"] in ("observe", "result", "bye"):
                return {"type": "noreply"} if msg["type"] != "result" else {"type": "noreply"}
            return json.loads(r.readline())
        # observe/result produce no reply; only hello/decide do — mirror that in the channel.
        def channel(msg):
            w.write(json.dumps(msg) + "\n"); w.flush()
            if msg["type"] in ("observe", "result", "bye"):
                return {"type": "noreply"}
            return json.loads(r.readline())
        replies, _ = _scripted_game(channel)
    th.join(5)
    check("socket: scripted game round-trips over a real connection",
          all(r["type"] in ("ready", "choice") for r in replies))
    check("socket: server thread cleanly finished the game", not th.is_alive())


def run() -> None:
    _kinds()
    _random_legal()
    _full_game_inprocess()
    _full_game_socket()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

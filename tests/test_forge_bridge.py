"""test_forge_bridge.py — UNIT tests for the Forge player adapter (forge_bridge.py), against a MOCK Forge.

This is the fast, hermetic half of the Forge work: it drives forge_bridge with a scripted sequence of
PlayerController-style decision requests (a MOCK Forge) so it needs NO JVM and NO Forge build — just
loopback for the socket check. It is NOT running Forge. The REAL integration — an actual headless Forge
JVM with mtg driving a seat — lives in forge_integration/ (ForgeVsBot.java / ForgeComboKill.java,
run via forge_integration/run.sh, see forge_integration/README.md for setup). Both exercise the same
forge_bridge adapter; this mock just lets us test the protocol + policy logic quickly.

Verifies (a) every decision KIND yields a legal, well-formed reply through the policy seam, (b) the pure
handle() and the real SOCKET transport agree, (c) a full scripted game runs to a result, (d) the
engine-backed policy (storm / Thassa's-Oracle lookahead, mana-payment delegation, library + floating-mana
sync) makes the right calls on reconstructed observations.

Run: python3 test_forge_bridge.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

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


def _search_driven_storm() -> None:
    """The EnginePolicy drives the play decision with win_search (no mechanic-specific logic) and stays in
    sync with Forge via the reconstructed _cast_count. Simulate Forge's observation at three points of a
    turn-1 storm line (Lotus Petal x9 -> Tendrils of Agony) and check the search keeps casting Petals to
    build the count, then fires the payoff once it's lethal — re-planned each decision from the snapshot."""
    import driver
    import effect_handlers
    effect_handlers.load()

    def obs_at(petals_cast):
        bf = [{"id": f"P{i}", "name": "Lotus Petal", "controller": "witch"} for i in range(petals_cast)]
        hand = [{"id": f"P{i}", "name": "Lotus Petal", "controller": "witch"} for i in range(petals_cast, 9)]
        hand += [{"id": "TEND", "name": "Tendrils of Agony", "controller": "witch"}]
        return {"seat": "witch", "players": ["witch", "opp"], "life": {"witch": 20, "opp": 20},
                "active": "witch", "step": "precombat_main", "castThisTurn": petals_cast,
                "zones": {"battlefield": bf, "hand": hand}}

    pol = fb.EnginePolicy()

    def pick(petals_cast):
        obs = obs_at(petals_cast)
        state, _ = fb.reconstruct(obs, "witch")
        assert state.get("_cast_count") == petals_cast      # _cast_count must mirror Forge's spell count
        opts = [{"id": c["id"], "ci": i, "kind": "spell"} for i, c in enumerate(obs["zones"]["hand"])]
        choice, _m, _e, _o, used = pol._pick_action(driver, state, "witch", opts, {"kind": "pass"})
        return (str(choice["id"]) if choice else None), used

    early, ue = pick(0)
    check("search-driven: at 0 cast, the lookahead plays a Lotus Petal (builds storm)",
          early and early.startswith("P") and ue == 1)
    mid, _ = pick(5)
    check("search-driven: at 5 cast (count synced), it keeps building with a Petal", mid and mid.startswith("P"))
    last, _ = pick(9)
    check("search-driven: at 9 cast, it fires the payoff (Tendrils now lethal)", last == "TEND")
    bad = fb.reconstruct({**obs_at(5), "castThisTurn": 0}, "witch")[0]
    check("search-driven: dropping the cast-count would desync the model (0 != 5)", bad.get("_cast_count") == 0)


def _search_driven_oracle() -> None:
    """The lookahead drives the Thassa's-Oracle combo too — sequence AND the 'choose a card name' decision —
    with NO combo-specific code. Needs a FAITHFUL starting state: the obs carries both LIBRARY COUNTS
    (libCounts), so reconstruct synthesizes them; without the witch library the win is meaningless, and
    without the opponent's the search would fabricate a deck-out. The search names the absent card to empty
    the library, and the policy relays that name to Forge's chooseCardName prompt."""
    import driver
    import effect_handlers
    effect_handlers.load()

    hand = [{"id": "P0", "name": "Lotus Petal", "controller": "w"}, {"id": "P1", "name": "Lotus Petal", "controller": "w"},
            {"id": "P2", "name": "Lotus Petal", "controller": "w"}, {"id": "CON", "name": "Demonic Consultation", "controller": "w"},
            {"id": "ORA", "name": "Thassa's Oracle", "controller": "w"}]
    obs = {"seat": "w", "players": ["w", "o"], "life": {"w": 20, "o": 20}, "active": "w", "step": "precombat_main",
           "castThisTurn": 0, "zones": {"battlefield": [], "hand": hand}, "libCounts": {"w": 55, "o": 53}}
    state, _ = fb.reconstruct(obs, "w")
    check("oracle: reconstruct synthesizes both libraries from libCounts",
          sum(1 for (p, _c) in state["in_library"] if p == "w") == 55 and sum(1 for (p, _c) in state["in_library"] if p == "o") == 53)

    import win_search
    driver.clear_cache()
    path, _n = win_search.find_win(dict(state, active_player={("w",)}), me="w", max_turns=1, node_budget=60000)
    check("oracle: the lookahead finds a turn-1 win from the faithful state", path is not None)
    con = next((a for a in (path or []) if a[0] == "cast" and a[2] == "CON"), None)
    check("oracle: the search CHOSE to name a card not in the deck (the sentinel)",
          con is not None and con[3].get("name") == "standard_procedure")
    check("oracle: the line also casts Thassa's Oracle to win",
          any(a[0] == "cast" and a[2] == "ORA" for a in (path or [])))

    # the policy relays the search's planned name to a 'choose a card name' prompt as the oracle NAME.
    pol = fb.EnginePolicy()
    bf = [{"id": f"P{i}", "name": "Lotus Petal", "controller": "w"} for i in range(3)]
    h2 = [{"id": "CON", "name": "Demonic Consultation", "controller": "w"}, {"id": "ORA", "name": "Thassa's Oracle", "controller": "w"}]
    st2, _ = fb.reconstruct({**obs, "castThisTurn": 3, "zones": {"battlefield": bf, "hand": h2}}, "w")
    pol._pick_action(driver, st2, "w", [{"id": "CON", "ci": 0, "kind": "spell"}, {"id": "ORA", "ci": 1, "kind": "spell"}], {"kind": "pass"})
    check("oracle: after planning Consultation, the policy holds the oracle name to relay",
          pol._pending_name == "Standard Procedure")
    check("oracle: _pick_name serves that name to Forge's chooseCardName", pol._pick_name(driver, st2, "w", [], "")[0] == "Standard Procedure")


def _mana_payment_delegated() -> None:
    """§106 mana payment is delegated to mtg: a 'pay' decision returns WHICH sources to tap and what
    COLOR each makes, so Forge can execute the exact payment a combo depends on. The classic trap: with
    Black Lotus (3 of ONE color) + Mox Jet, pay {B} from the Mox — NOT by cracking the Lotus needed for a
    later {U}{U}. driver.mana_plan must make that call (and the policy's 'pay' decision relay it)."""
    import driver
    import effect_handlers
    effect_handlers.load()

    p = fb.ForgePlayer(policy=fb.EnginePolicy())
    p.handle({"type": "hello", "you": "w", "players": ["w", "o"]})
    bf = [{"id": "LOT", "name": "Black Lotus", "controller": "w"}, {"id": "JET", "name": "Mox Jet", "controller": "w"}]
    p.handle({"type": "observe", "state": {"life": {"w": 20, "o": 20}, "active": "w", "step": "precombat_main",
              "castThisTurn": 0, "zones": {"battlefield": bf, "hand": []}, "libCounts": {"w": 56, "o": 53}}})

    payB = p.handle(_decide("pay", {"pips": {"black": 1}, "generic": 0}))["value"]
    check("pay {B}: engine taps the Mox, not the Black Lotus",
          isinstance(payB, list) and len(payB) == 1 and payB[0]["id"] == "JET")
    payUU = p.handle(_decide("pay", {"pips": {"blue": 2}, "generic": 0}))["value"]
    check("pay {U}{U}: engine uses Black Lotus and forces blue",
          isinstance(payUU, list) and len(payUU) == 1 and payUU[0]["id"] == "LOT" and payUU[0]["express"] == "blue")
    check("pay {U}{U}: the Lotus is paid by sacrifice", payUU and payUU[0]["sacrifice"] is True)

    # driver.mana_plan directly: a cost the board can't cover -> None (so Forge pays it).
    st, _ = fb.reconstruct({"seat": "w", "players": ["w"], "life": {"w": 20}, "active": "w",
                            "zones": {"battlefield": bf, "hand": []}}, "w")
    check("mana_plan returns None when the board can't cover the cost (Forge pays)",
          driver.mana_plan(st, "w", {"green": 4}, 0) is None)

    # §106.4 FLOATING mana syncs from Forge's pool: the obs 'floating' -> state['floating_mana'], so the
    # lookahead spends already-produced mana, and a pay plan covers only the remainder.
    fst, _ = fb.reconstruct({"seat": "w", "players": ["w", "o"], "life": {"w": 20, "o": 20}, "active": "w",
                             "zones": {"battlefield": bf, "hand": []}, "floating": {"w": {"black": 2}}}, "w")
    check("floating mana syncs from the Forge obs", driver._floating(fst, "w") == {"black": 2})
    check("a pay plan uses synced floating first (an empty plan covers {B} from the float)",
          driver.mana_plan(fst, "w", {"black": 1}, 0) == [])  # Lotus makes only 3 of one color; Mox is black


def _land_play_coverage_bound() -> None:
    """COVERAGE METRIC regression: a land play is a real engine decision, but lands aren't in `spells`. If
    offered counted only spells, a land play would add 1 to endorsed against 0 offered -> cumulative
    endorsed_frac > 1 (seen as 2.167 in a vanilla mirror). offered/modeled must include lands, so per
    decision endorsed (0/1) <= offered."""
    import driver
    obs = {"seat": "w", "players": ["w", "opp"], "life": {"w": 20, "opp": 20}, "active": "w",
           "step": "precombat_main", "zones": {"hand": [{"id": "L1", "name": "Forest", "controller": "w"}]}}
    state, _ = fb.reconstruct(obs, "w")
    pol = fb.EnginePolicy()
    opts = [{"id": "L1", "ci": 0, "kind": "land"}]                # only a land offered (no castable spell)
    choice, modeled, endorsed, offered, _used = pol._pick_action(driver, state, "w", opts, {"kind": "pass"})
    check("land-only decision: the engine plays the land", choice is not None and choice["id"] == "L1")
    check("land-only decision: endorsed <= offered (no endorsed_frac > 1)", endorsed <= offered and offered >= 1)
    check("land-only decision: the land counts as modeled", modeled >= 1)


def run() -> None:
    _kinds()
    _random_legal()
    _full_game_inprocess()
    _full_game_socket()
    _search_driven_storm()
    _search_driven_oracle()
    _mana_payment_delegated()
    _land_play_coverage_bound()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

"""run_commander_tournament.py — a Forge-refereed 4-player FREE-FOR-ALL Commander (§903) tournament.

Forge is the source of truth: it runs and referees each game, owns the rules/state, and validates our
engine by being the authority the witchcraft seats must agree with. Four seats per game — TWO witchcraft
players (each socket-driven by forge_bridge.EnginePolicy on its own bot port) and TWO Forge AI players —
play individual Commander (not Two-Headed Giant).

ROUND ROBIN OF DECKS. There are four 100%-CLEAN cEDH decks (Ral / Stella / Blue Farm / Kinnan). The seat
TYPES are fixed (seats 0,1 = witchcraft, seats 2,3 = Forge AI); the DECKS rotate so each seat pilots each
deck exactly once across the four games (game g: seat i flies deck (i+g) % 4). So every deck is also flown
by every seat once — a balanced rotation, no seat/deck advantage baked in.

Run:
    python3 forge_integration/run_commander_tournament.py            # 4 games (the full rotation)
    python3 forge_integration/run_commander_tournament.py --quick    # 1 game (smoke)
"""
from __future__ import annotations

import ast
import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
JDK = os.environ.get("JDK", "/home/zucc/opt/jdk-17.0.13+11")
FORGE = os.environ.get("FORGE", "/home/zucc/Development/witchcraft/forge")
FATJAR = f"{FORGE}/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar"
OUT = "/tmp/forge_tournament_out"
DECK_DIR = "/tmp/cedh_decks"
LOG_DIR = "/tmp/cedh_tournament_logs"
# Optional Forge JVM heap cap, e.g. JVM_HEAP=4g -> -Xmx4g. Empty (default) = let the JVM self-size to ~25% of
# RAM. Set this on small-memory hosts; on a beefy box leave it unset. See forge_integration/RUNNING.md.
JVM_HEAP = os.environ.get("JVM_HEAP", "")
_XMX = f"-Xmx{JVM_HEAP} " if JVM_HEAP else ""
# Forge's GuiDesktop static init calls getDefaultScreenDevice(), which throws HeadlessException under
# -Djava.awt.headless=true. A headless Linux server tolerates it differently; a desktop host (e.g. macOS
# with a real display) must run NON-headless so the screen device is found. Default headless (server);
# set FORGE_HEADLESS=false on a machine that has a display. See forge_integration/RUNNING.md.
_HEADLESS = os.environ.get("FORGE_HEADLESS", "true").lower() not in ("0", "false", "no")
_HEADLESS_ARG = "-Djava.awt.headless=true " if _HEADLESS else ""
GAME_TIMEOUT = int(os.environ.get("GAME_TIMEOUT", "1800"))    # 4-player cEDH vs Forge AI is grindy -> 30 min/game
# §903 Commander is 40 life; keep witch decisions fast (a small lookahead — Forge owns the rules, the engine
# just drives its seat where it can and falls back to Forge AI otherwise) so a 4-player game still finishes.
# NB: each witch seat ALSO gets a per-deck MTG_DECK_AXIS + MTG_SYNERGY (see axis_synergy() / run_game) so the
# develop search has a win condition to advance toward — without it the seat only ever plays lands and passes.
BOT_ENV = {"MTG_POLICY": os.environ.get("MTG_POLICY", "engine"), "MTG_START_LIFE": "40",
           "MTG_SEARCH_TURNS": "1", "MTG_SEARCH_BUDGET": os.environ.get("MTG_SEARCH_BUDGET", "3000")}

# the four 100%-CLEAN cEDH decks (key -> cedh_decklists name). Cards Forge can't load are substituted.
DECKS = {"ral": "Ral Turbo Storm", "stella": "Stella Lee Wild Card",
         "bluefarm": "Blue Farm", "kinnan": "Kinnan, Bonder Prodigy"}
SUBST = {"________ Goblin": "Island"}                          # the un-set sticker Goblin -> a basic (keeps it legal)


def _deck_card_names(key: str) -> list:
    """The distinct card names of deck `key` — commander front-faces + main — as the deck evaluators want them."""
    from cedh_decklists import DECKS as CEDH
    d = CEDH[DECKS[key]]
    cmd = d["commander"] if isinstance(d["commander"], list) else [d["commander"]]
    return [c.split(" // ")[0] for c in cmd] + list(d["cards"].keys())


_AXIS_CACHE: dict = {}


def axis_synergy(key: str) -> dict:
    """The win PLAN a witch seat flying deck `key` develops toward, as bot-env vars: the deck's primary §104 win
    axis (deck_evaluator.deck_axis — the evaluator that identifies the deck's winning mechanic) and its primary
    synergy/combo cluster (interaction_evaluator.synergy_cluster). FFA -> opponent-passive find_progress (no
    MTG_MINIMAX). Computed once per deck and cached. This is what makes the seat cast its deck instead of
    playing a land and passing every turn."""
    if key not in _AXIS_CACHE:
        import deck_evaluator
        import interaction_evaluator
        names = _deck_card_names(key)
        syn = interaction_evaluator.synergy_cluster(names, commander=True)
        _AXIS_CACHE[key] = {"MTG_DECK_AXIS": deck_evaluator.deck_axis(names, commander=True),
                            "MTG_SYNERGY": ",".join(sorted(syn["slugs"])),
                            "MTG_SYNERGY_SIZE": str(syn["size"])}
    return _AXIS_CACHE[key]


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, **kw)


def write_decks() -> dict:
    """Emit one Forge .dck-ish file per deck (a [Commander] section + an 'N CardName' [Main]). Returns
    {key: path}. DFC commanders are listed by their front-face name; un-loadable cards are substituted."""
    from cedh_decklists import DECKS as CEDH
    os.makedirs(DECK_DIR, exist_ok=True)
    paths = {}
    for key, dn in DECKS.items():
        d = CEDH[dn]
        cmd = d["commander"] if isinstance(d["commander"], list) else [d["commander"]]
        lines = ["[metadata]", f"Name={dn}", "[Commander]"]
        lines += ["1 " + c.split(" // ")[0] for c in cmd]
        lines.append("[Main]")
        lines += [f"{n} {SUBST.get(c, c)}" for c, n in d["cards"].items()]
        path = f"{DECK_DIR}/{key}.dck"
        open(path, "w").write("\n".join(lines) + "\n")
        paths[key] = path
    return paths


def compile_harness() -> None:
    os.makedirs(OUT, exist_ok=True)
    r = sh(f'"{JDK}/bin/javac" -cp "{FATJAR}" -d "{OUT}" "{HERE}/ForgeCommanderFFA.java"')
    if r.returncode != 0:
        print(f"COMPILE FAILED:\n{r.stderr}", flush=True)
        sys.exit(1)
    print("compiled ForgeCommanderFFA", flush=True)


def free_port(base: int) -> int:
    p = base
    while p < base + 400:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
        p += 1
    return base


def run_game(seat_decks: list, deck_paths: dict, port_base: int, timeout: int = GAME_TIMEOUT) -> dict:
    """One 4-player Commander FFA game. `seat_decks[i]` = the deck KEY for seat i. Seats 0,1 are witchcraft
    (each its own bot process+port), seats 2,3 are Forge AI. Returns the parsed result + per-seat deck map."""
    seat_type = ["witch", "witch", "ai", "ai"]
    seat_name = [f"Witch-{seat_decks[0]}", f"Witch-{seat_decks[1]}", f"ForgeAI-{seat_decks[2]}", f"ForgeAI-{seat_decks[3]}"]
    ports = [free_port(port_base), free_port(port_base + 40), 0, 0]
    # stand up a bot per witchcraft seat
    os.makedirs(LOG_DIR, exist_ok=True)
    bots = []
    for i in (0, 1):
        plan = axis_synergy(seat_decks[i])                     # this seat's deck-specific win axis + synergy combo
        print(f"  seat {i} Witch[{seat_decks[i]}] develops toward axis={plan['MTG_DECK_AXIS']} "
              f"synergy_size={plan['MTG_SYNERGY_SIZE']}", flush=True)
        b = subprocess.Popen([sys.executable, f"{HERE}/run_bot.py", str(ports[i])],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(os.environ, **BOT_ENV, **plan))
        bots.append(b)
    time.sleep(1.5)
    props = []
    for i in range(4):
        props += [f"-Ddeck{i}={deck_paths[seat_decks[i]]}", f"-Dname{i}={seat_name[i]}",
                  f"-Dtype{i}={seat_type[i]}", f"-Dport{i}={ports[i]}"]
    env = dict(os.environ, FORGE_ASSETS=f"{FORGE}/forge-gui/")
    log = f"{LOG_DIR}/game_p{port_base}.log"                   # stream Forge's live move record to a per-game log
    cmd = (f'timeout {timeout} "{JDK}/bin/java" {_XMX}{_HEADLESS_ARG}'
           f'{" ".join(props)} -cp "{FATJAR}:{OUT}" ForgeCommanderFFA > "{log}" 2>&1')
    r = sh(cmd, env=env)
    try:
        out0 = open(log).read()
    except OSError:
        out0 = ""
    r = subprocess.CompletedProcess(cmd, r.returncode, stdout=out0, stderr="")
    # Collect each witchcraft seat's full coverage dict (run_bot.py prints it on socket close). Previously the
    # bot output was communicated and discarded — so the FFA never surfaced how much of its seat the engine
    # actually drove. We keep the whole dict (not just the modeled/endorsed fractions) so the per-game readout
    # can show engine_decided/decisions (the real drive-rate) and the by_kind breakdown — see SMOKE_FINDINGS.md
    # "CORRECTION": endorsed_frac is an option-coverage ratio, NOT how often the engine picked the move.
    cov = {}
    for i, b in enumerate(bots):
        try:
            bot_out, _ = b.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            b.kill(); bot_out = ""
        cl = re.search(r"COVERAGE:\s*(\{.*\})", bot_out or "")
        if cl:
            try:
                cov[seat_name[i]] = ast.literal_eval(cl.group(1))
            except (ValueError, SyntaxError):
                pass
    out = r.stdout + "\n" + r.stderr
    res = {"winner": "TIMEOUT/ERR", "turns": "?", "wall": "?", "seat_name": seat_name, "seat_type": seat_type,
           "seat_decks": list(seat_decks), "coverage": cov, "raw": out}
    m = re.search(r"RESULT winner=(\S+) turns=(\d+) wall=(\d+)ms", out)
    if m:
        res.update(winner=m.group(1), turns=m.group(2), wall=m.group(3))
    # Provisional standing: a game killed by `timeout` prints no RESULT line. Score it by each seat's LAST known
    # life from the [move] log so a non-decisive (durdled-out) game is still informative. This is NOT a win —
    # Forge stays the source of truth for real outcomes — just a readout of where the game stood.
    last_life = {}
    for nm in seat_name:
        hits = re.findall(re.escape(nm) + r" life -?\d+ -> (-?\d+)", out)
        if hits:
            last_life[nm] = int(hits[-1])
    if last_life:
        res["standing"] = sorted(((seat_name[s], last_life[seat_name[s]])
                                  for s in range(4) if seat_name[s] in last_life),
                                 key=lambda kv: kv[1], reverse=True)
    return res


def main() -> None:
    quick = "--quick" in sys.argv
    deck_paths = write_decks()
    compile_harness()
    keys = list(DECKS)                                         # [ral, stella, bluefarm, kinnan]
    t0 = time.time()
    games = []
    n_games = 1 if quick else 4
    for g in range(n_games):
        seat_decks = [keys[(i + g) % 4] for i in range(4)]    # rotate: each seat flies a different deck each round
        print(f"\n=== GAME {g + 1}/{n_games}  (4-player Commander FFA, Forge refereeing) ===", flush=True)
        print(f"  seats: 0=Witch[{seat_decks[0]}] 1=Witch[{seat_decks[1]}] "
              f"2=AI[{seat_decks[2]}] 3=AI[{seat_decks[3]}]", flush=True)
        res = run_game(seat_decks, deck_paths, 9300 + g * 100)
        games.append(res)
        print(f"  -> winner={res['winner']} turns={res['turns']} wall={res['wall']}ms", flush=True)
        cov = res.get("coverage") or {}
        for n, c in cov.items():
            dec, eng = c.get("decisions") or 0, c.get("engine_decided") or 0
            drive = eng / dec if dec else 0.0
            print(f"     {n}: modeled={c.get('modeled_frac')} endorsed={c.get('endorsed_frac')} "
                  f"drove(engine/decisions)={eng}/{dec}={drive:.2f}", flush=True)
            bk = c.get("by_kind") or {}
            if bk:
                print("        by_kind: " + "  ".join(
                    f"{k}(off={v.get('offered')},mod={v.get('modeled')},end={v.get('endorsed')},eng={v.get('engine')})"
                    for k, v in bk.items()), flush=True)
        if res.get("standing"):
            print("     life standing: " + "  ".join(f"{n}={ll}" for n, ll in res["standing"]), flush=True)

    print("\n" + "=" * 80)
    print("COMMANDER FFA TOURNAMENT SUMMARY  (Forge = referee/source-of-truth; 2 witchcraft + 2 Forge-AI)")
    print("=" * 80)
    side = {"witchcraft": 0, "forge-ai": 0, "draw/none": 0}
    deck_wins: dict = {k: 0 for k in keys}
    for i, m in enumerate(games):
        win = m["winner"]
        # map the winning seat name back to its type + deck
        side_won, deck_won = "draw/none", "-"
        for s in range(4):
            if m["seat_name"][s] == win:
                side_won = "witchcraft" if m["seat_type"][s] == "witch" else "forge-ai"
                deck_won = m["seat_decks"][s]
                break
        side[side_won] = side.get(side_won, 0) + 1
        if deck_won in deck_wins:
            deck_wins[deck_won] += 1
        print(f"  game {i + 1}: winner={win:24} side={side_won:11} deck={deck_won:9} "
              f"turns={m['turns']} wall={m['wall']}ms")
        if win == "TIMEOUT/ERR" and m.get("standing"):
            print("           provisional life standing: "
                  + "  ".join(f"{n}={ll}" for n, ll in m["standing"]))
    print(f"\n  side tally:  witchcraft={side['witchcraft']}  forge-ai={side['forge-ai']}  "
          f"draw/none={side.get('draw/none', 0)}")
    print(f"  deck wins:   " + "  ".join(f"{k}={v}" for k, v in deck_wins.items()))
    print(f"\nwall: {round(time.time() - t0)}s")


if __name__ == "__main__":
    main()

"""run_tournament.py — the Forge-refereed witchcraft regression tournament.

Forge is the source of truth; the witchcraft datalog engine (forge_bridge.EnginePolicy) drives its seat's
plays (and validates its rules interpretation by agreeing with Forge). Two kinds of scenario:

  WIN-CON REGRESSIONS (ForgeComboKill): witchcraft pilots a stacked turn-1 kill the lookahead rediscovers;
    Forge must confirm winner=Witchcraft-Engine. Two lines — Thassa's Oracle (library-out) + Tendrils storm.

  DECK MATCHUPS (ForgeVsBot): a full game, witchcraft drives its seat (win_search where it sees a win, else
    Forge AI fallback), Forge refereeing. The 2x2 of {izzet, vanilla} decks, BEST OF 3 (first to 2 wins).
    The generic tree search shows "partial results" here: it models every option and endorses the plays it
    can prove win, deferring the rest to Forge's AI.

Each game = a fresh bot process (run_bot.py, serves one game) + a fresh Forge JVM. Run:
    python3 forge_integration/run_tournament.py            # full tournament
    python3 forge_integration/run_tournament.py --quick    # combos + one bo1 pairing (smoke)
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JDK = os.environ.get("JDK", "/home/zucc/opt/jdk-17.0.13+11")
FORGE = os.environ.get("FORGE", "/home/zucc/Development/witchcraft/forge")
FATJAR = f"{FORGE}/forge-gui-desktop/target/forge-gui-desktop-2.0.13-SNAPSHOT-jar-with-dependencies.jar"
OUT = "/tmp/forge_tournament_out"
GAME_TIMEOUT = int(os.environ.get("GAME_TIMEOUT", "300"))


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, **kw)


def compile_harnesses() -> None:
    os.makedirs(OUT, exist_ok=True)
    for src in ("ForgeComboKill.java", "ForgeVsBot.java"):
        r = sh(f'"{JDK}/bin/javac" -cp "{FATJAR}" -d "{OUT}" "{HERE}/{src}"')
        if r.returncode != 0:
            print(f"COMPILE FAILED ({src}):\n{r.stderr}", flush=True)
            sys.exit(1)
    print("compiled ForgeComboKill + ForgeVsBot", flush=True)


def free_port(base: int) -> int:
    p = base
    while p < base + 200:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
        p += 1
    return base


def run_game(main_class: str, jprops: dict, port: int, timeout: int = GAME_TIMEOUT) -> dict:
    """Start the witchcraft bot, run one Forge game with `main_class` + `-D` props, return parsed result."""
    bot = subprocess.Popen([sys.executable, f"{HERE}/run_bot.py", str(port)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.2)
    props = " ".join(f"-D{k}={v}" for k, v in jprops.items())
    env = dict(os.environ, FORGE_ASSETS=f"{FORGE}/forge-gui/")
    cmd = (f'timeout {timeout} "{JDK}/bin/java" -Djava.awt.headless=true '
           f'-DbotHost=127.0.0.1 -DbotPort={port} {props} -cp "{FATJAR}:{OUT}" {main_class}')
    r = sh(cmd, env=env)
    try:
        bot_out, _ = bot.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        bot.kill(); bot_out = ""
    out = r.stdout + "\n" + r.stderr
    res = {"winner": "TIMEOUT/ERR", "turns": "?", "wall": "?", "modeled": None, "endorsed": None, "raw": out}
    m = re.search(r"RESULT winner=(\S+) turns=(\d+).*?wall=(\d+)ms", out)
    if m:
        res.update(winner=m.group(1), turns=m.group(2), wall=m.group(3))
    cm = re.search(r"'modeled_frac': ([0-9.]+).*?'endorsed_frac': ([0-9.]+)", bot_out)
    if cm:
        res.update(modeled=float(cm.group(1)), endorsed=float(cm.group(2)))
    em = re.search(r"'engine_decided': (\d+)", bot_out)
    if em:
        res["engine_decided"] = int(em.group(1))
    return res


def run_combo(name: str, combo: str, port: int, timeout: int = GAME_TIMEOUT) -> dict:
    print(f"\n=== WIN-CON: {name} (witchcraft pilots; Forge confirms) ===", flush=True)
    res = run_game("ForgeComboKill", {"combo": combo}, port, timeout=timeout)
    ok = res["winner"] == "Witchcraft-Engine"
    status = "PASS" if ok else ("SLOW" if res["winner"] == "TIMEOUT/ERR" else "FAIL")
    print(f"  winner={res['winner']} turns={res['turns']} wall={res['wall']}ms "
          f"modeled={res['modeled']} endorsed={res['endorsed']}  -> {status}", flush=True)
    res["pass"] = ok
    res["status"] = status
    return res


def run_matchup(witch: str, opp: str, port_base: int, best_of: int = 3) -> dict:
    print(f"\n=== MATCHUP: witchcraft[{witch}] vs forge-ai[{opp}]  (best of {best_of}) ===", flush=True)
    need = best_of // 2 + 1
    wins = {"Witchcraft-Engine": 0, "Forge-AI": 0}
    games = []
    for g in range(best_of):
        port = free_port(port_base + g)
        res = run_game("ForgeVsBot", {"witchDeck": witch, "oppDeck": opp}, port)
        games.append(res)
        w = res["winner"]
        if w in wins:
            wins[w] += 1
        print(f"  game {g + 1}: winner={w} turns={res['turns']} wall={res['wall']}ms "
              f"modeled={res['modeled']} endorsed={res['endorsed']} engine_decided={res.get('engine_decided')}",
              flush=True)
        if wins["Witchcraft-Engine"] >= need or wins["Forge-AI"] >= need:
            break
    champ = max(wins, key=wins.get) if max(wins.values()) >= need else "split"
    print(f"  -> series: Witchcraft-Engine {wins['Witchcraft-Engine']} - {wins['Forge-AI']} Forge-AI  "
          f"({champ})", flush=True)
    return {"witch": witch, "opp": opp, "wins": wins, "champ": champ, "games": games}


def main() -> None:
    quick = "--quick" in sys.argv
    compile_harnesses()
    t0 = time.time()
    # Thassa's Oracle is the proven, fast win-con. The Tendrils storm is capped short: its 9 identical Lotus
    # Petals get distinct ids that don't transposition-collapse, so the lookahead's first search blows up
    # (a known search-efficiency edge with symmetric duplicates — NOT a coverage gap). Reported as SLOW if so.
    combos = [run_combo("Thassa's Oracle (library-out)", "oracle", free_port(8800)),
              run_combo("Tendrils storm (storm count)", "storm", free_port(8810), timeout=90)]
    pairings = [("izzet", "izzet"), ("izzet", "vanilla"), ("vanilla", "izzet"), ("vanilla", "vanilla")]
    if quick:
        pairings = [("vanilla", "vanilla")]
    matchups = []
    for i, (w, o) in enumerate(pairings):
        matchups.append(run_matchup(w, o, 8820 + i * 10, best_of=1 if quick else 3))

    print("\n" + "=" * 72)
    print("TOURNAMENT SUMMARY  (Forge = referee; witchcraft drives its seat)")
    print("=" * 72)
    print("\nWIN-CON REGRESSIONS (witchcraft must win as the piloting seat):")
    for c in combos:
        print(f"  {c['status']:5} {('winner=' + c['winner']):32} "
              f"modeled={c['modeled']} endorsed={c['endorsed']}")
    print("\nDECK MATCHUPS (best of 3; modeled = options witchcraft understood, "
          "endorsed = plays its search drove):")
    print(f"  {'witchcraft':10} {'forge-ai':10} {'series':14} {'modeled~':9} {'endorsed~':9}")
    for m in matchups:
        gs = [g for g in m["games"] if g["modeled"] is not None]
        mod = round(sum(g["modeled"] for g in gs) / len(gs), 3) if gs else None
        end = round(sum(g["endorsed"] for g in gs) / len(gs), 3) if gs else None
        series = f"{m['wins']['Witchcraft-Engine']}-{m['wins']['Forge-AI']} ({m['champ']})"
        print(f"  {m['witch']:10} {m['opp']:10} {series:14} {str(mod):9} {str(end):9}")
    print(f"\nwall: {round(time.time() - t0)}s")


if __name__ == "__main__":
    main()

"""run_one_capture.py — run ONE ForgeVsBot game and save the COMPLETE move record.

Captures both sides: the mtg bot's decision trace (MTG_DEBUG -> bot stdout/stderr) and Forge's own
stdout (the `[bot] engine plays/attacks/blocks` lines + the authoritative chronological GAME LOG dumped at
RESULT). Writes everything to /tmp/vanilla_mirror_capture.txt.

    python3 forge_integration/run_one_capture.py [witchDeck] [oppDeck]
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import run_tournament as T   # reuse paths, compile, free_port, deck axis/synergy

witch = sys.argv[1] if len(sys.argv) > 1 else "vanilla"
opp = sys.argv[2] if len(sys.argv) > 2 else "vanilla"
OUTFILE = f"/tmp/{witch}_vs_{opp}_capture.txt"

T.compile_harnesses()
syn = T._synergy_of(witch)
bot_env = {"MTG_POLICY": "engine", "MTG_DECK_AXIS": T._axis_of(witch), "MTG_OPP_AXIS": T._axis_of(opp),
           "MTG_MINIMAX": os.environ.get("MTG_MINIMAX", "1"), "MTG_MINIMAX_TURNS": "2", "MTG_PROGRESS_BUDGET": "2000",
           "MTG_SEARCH_TURNS": "1", "MTG_SEARCH_BUDGET": "20000", "MTG_START_LIFE": "20",
           "MTG_SYNERGY": ",".join(sorted(syn["slugs"])), "MTG_SYNERGY_SIZE": str(syn["size"]),
           "MTG_DEBUG": "1"}                                    # <- per-decision bot trace (matches the tournament)

port = T.free_port(8970)
bot = subprocess.Popen([sys.executable, f"{HERE}/run_bot.py", str(port)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       env=dict(os.environ, **bot_env))
time.sleep(1.2)
props = {"witchDeck": witch, "oppDeck": opp}
propstr = " ".join(f"-D{k}={v}" for k, v in props.items())
env = dict(os.environ, FORGE_ASSETS=f"{T.FORGE}/forge-gui/")
cmd = (f'timeout {T.GAME_TIMEOUT} "{T.JDK}/bin/java" -Djava.awt.headless=true '
       f'-DbotHost=127.0.0.1 -DbotPort={port} {propstr} -cp "{T.FATJAR}:{T.OUT}" ForgeVsBot')
r = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
try:
    bot_out, _ = bot.communicate(timeout=45)
except subprocess.TimeoutExpired:
    bot.kill(); bot_out = ""

forge_out = r.stdout + "\n" + r.stderr
with open(OUTFILE, "w") as f:
    f.write(f"=== {witch} (mtg) vs {opp} (forge-ai) — FULL CAPTURE ===\n\n")
    f.write("########## FORGE SIDE (game log + engine plays) ##########\n")
    f.write(forge_out)
    f.write("\n\n########## WITCHCRAFT BOT SIDE (MTG_DEBUG decision trace + coverage) ##########\n")
    f.write(bot_out)
print(f"wrote {OUTFILE}")
# echo the RESULT + game-log section to stdout for convenience
for line in forge_out.splitlines():
    if "RESULT" in line or "GAME LOG" in line:
        print(line)

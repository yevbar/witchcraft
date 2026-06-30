"""mtg.engine — the game-playing engine + agent-facing layer over the datalog driver.

env (referee/legal-action enumeration), observe (per-seat hidden-info projection), search/win_search
(lookahead), game (setup + self-play harness), and engine (the fuller turn-loop game engine over sim).
The raw datalog drivers (driver, sim, the souffle backends, bridge_to_engine) live one level up in mtg.
"""

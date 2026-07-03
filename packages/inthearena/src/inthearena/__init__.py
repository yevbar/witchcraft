"""inthearena — a research bridge from MTG Arena's game log to a decision Policy.

`inthearena.mtga` parses MTGA's detailed client log (its GRE message stream) into typed (pydantic) game
state and decision points, then lets a decision **Policy** pick from the legal options the client already
surfaces. Run read-only over your own logs ("shadow" mode):

    from inthearena.mtga import iter_decisions, AggroPolicy
    pol = AggroPolicy()
    for d in iter_decisions():            # typed decisions from your local Player.log
        print(d.view.phase, d.kind, "->", pol.decide(d))

Optional extras layer on capability:
  * `inthearena[engine]` — drive the `python-mtg` rules engine (EnginePolicy, `to_game`/`suggest`).
  * `inthearena[act]`    — drive the live client via mouse/keyboard (pyautogui).
  * `inthearena[vision]` — a local vision model to locate on-screen elements.

Driving the live client is OPT-IN and against MTGA's Terms of Service — see DISCLAIMER.md. Gameplay only.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

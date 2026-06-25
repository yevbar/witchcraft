# inthearena

A **research** bridge that drives *Magic: The Gathering* agents from real **MTG Arena** game data.

`inthearena.mtga` parses MTGA's detailed client log (its GRE message stream) into typed (pydantic) game state
and decision points, then lets a decision **Policy** pick from the legal options the client is already handed.
The first wired policy is `AggroPolicy` — a relentless develop-and-attack bot — run **read-only** over your own
game logs ("shadow" mode).

```python
from inthearena.mtga import iter_decisions, AggroPolicy, cards

pol = AggroPolicy()
for d in iter_decisions():            # typed decisions from your local Player.log
    print(d.view.phase, d.kind, "->", pol.decide(d))

cards.card_name(79416)                # -> 'Arcane Signet'  (grpId -> name, from the local card DB)
```

```bash
python -m inthearena.mtga.shadow      # replay your games; print what aggro would do at each decision
```

## ⚠️ Read this first

This project reads / can automate a live commercial game client. **Read [DISCLAIMER.md](DISCLAIMER.md) before
using it.** In short: automating the MTGA client violates its Terms of Service (and can get an account banned),
prior art already exists, and this is shared for research — treat it sensitively, and prefer the read-only mode.

## What's here

- `mtga/gre.py` — parse `Player.log` into typed GRE objects; diff-accurate gameplay state; surface decisions.
- `mtga/snapshot.py` — the gameplay state as a readable snapshot and as `mtg`-engine facts.
- `mtga/policy.py` — `Policy` protocol + `AggroPolicy` (chooses from MTGA's legal menu; no rules engine needed).
- `mtga/cards.py` — resolve `grpId` → English card name from the client's local SQLite card database.
- `mtga/engine.py` — position an `mtg.Game` at the current board by determinization (visible info fed, hidden
  zones random-filled); format-aware (Brawl vs Standard → engine variant + commander).
- `mtga/views.py` — `RecognizedViews` (Home, Recently played) + where each view's clickable elements sit.
- `mtga/screen.py` — recognize the current view from the latest log scene and/or a pluggable image model.
- `mtga/navigate.py` — drive a non-game view INTO a game via a pluggable `Actuator` (no-op by default; the
  real pyautogui backend is opt-in, `pip install inthearena[act]`, and ToS-relevant — see DISCLAIMER.md).
- `mtga/live.py` — follow `Player.log` as it's written (tail -f); `LiveState` keeps board + view current.
- `mtga/shadow.py` — run a policy read-only over a log.

## Status

Early research. The **read + decide** half (log → typed state → policy choice, with card names) works today.
Driving the live client to actually play is intentionally **not** included here — that is the ToS-relevant
piece (see the disclaimer).

## Install (dev)

```bash
pip install -e .          # needs pydantic>=2
PYTHONPATH=src python3 tests/test_mtga.py
```

Distributed on PyPI as `inthearena` (placeholder release; the real API lands in a later version). MIT licensed.

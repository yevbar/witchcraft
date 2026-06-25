# Disclaimer & Responsible-Use Notice

`inthearena` is a **research** project. It bridges decision-making agents to *Magic: The Gathering Arena*
(MTGA) by reading the client's detailed game logs and — potentially — driving the desktop client. Please read
this before using it, and treat the project sensitively.

## Terms of Service / account risk

Automating the MTGA client is against Wizards of the Coast's Terms of Service and End-User License Agreement.

- The **read-only log analysis** in this repo ("shadow" mode) only parses the `Player.log` the client already
  writes — the same data trackers have read for years. It does not touch or control the game.
- Any **active client-driving** mode (screen automation that actually plays) is the part that violates the ToS
  and can result in account suspension or a permanent ban.

The author shares this openly and accepts that risk for the sake of publishable research. **Anyone who runs an
active/driving mode does so entirely at their own risk.**

## Prior art

This project does not exist in a vacuum — Magic tooling and automation are already public:

- **Log-reading trackers** (e.g. MTGArena-Tool, 17Lands-style overlays) parse the same `Player.log` for deck,
  draft, and collection data. Read-only and widely used.
- **Client-driving bots** exist (e.g. `Barrylim366/mtga-farm-bot`) combining log-derived state with screen
  automation (pyautogui / OpenCV).
- **Forge** is an open-source MTG rules engine and the only practical way to play full games against a
  programmable opponent today — but it runs *its own* engine in a JVM (with non-trivial setup and headaches)
  and is **not** the live MTGA client.

There is no viable published bot that plays the *actual* MTGA client. That gap — together with Magic's standing
as a studied domain in computer science (the game is Turing-complete and a recognized hard problem for AI) — is
the research motivation here.

## Intent & ethics

This is shared for **transparency, auditability, and contribution to research** — not to gain an unfair
competitive advantage, farm rewards, or disrupt other players. Specifically:

- Prefer the **read-only shadow mode**; it is non-invasive.
- Do **not** deploy automated play against real human opponents on the competitive ladder.
- If you build on this for research, **disclose and cite** it.

If you are unsure whether a use is appropriate, default to not doing it.

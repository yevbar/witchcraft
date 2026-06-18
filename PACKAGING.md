# Packaging `witchcraft` like python-chess — deferred work

The `witchcraft/` package gives an importable, python-chess-shaped API (`witchcraft.Game`). What it does
*not* yet do is install and run cleanly outside the repository the way `pip install python-chess` does. This
file records the gap, so the packaging step can be picked up without re-discovering it.

python-chess is the bar because it's **pure Python**: `pip install`, `import chess`, done — no compiler, no
native artifact, no working-directory assumption. Our engine is the opposite: a Datalog program compiled by a
souffle fork down to C++ and built into a per-program `.so`. That's the whole hurdle.

## What blocks a clean `pip install witchcraft`

1. **cwd-relative data paths.** `engine_native._SRC = Path("datalog/engine_rules.dl")` and the souffle fork
   path are resolved relative to the *current working directory*, so the package only works when run from the
   repo root. Fix: resolve these via `importlib.resources` against the package, and ship `datalog/` as package
   data. This is the cheapest, highest-value step and is independent of the native-build question.

2. **Engine modules live at the repo root, not in the package.** `driver`, `env`, `game`,
   `bridge_to_engine`, `engine_*` are top-level modules that cross-import by bare name. The package adds the
   repo root to `sys.path` so `import driver` works, but a real wheel needs them *inside* the package (e.g.
   `witchcraft._engine.driver`) with the bare imports rewritten, or kept as a separately-published
   `witchcraft-engine` dependency.

3. **The native artifact.** First use compiles the Datalog → C++ → `.so` with a local `g++`/`clang++` and the
   souffle fork's headers (a one-time ~4–7 min build, cached in `/tmp` by content hash). A `pip` user has
   neither the souffle fork nor necessarily a C++ toolchain. Options, roughly in order of effort:
   - **Ship the prebuilt `.so`** as package data per (platform, arch) — fast import, but it's
     `engine_rules.dl`-specific, so it must be rebuilt and re-shipped whenever the rules change.
   - **Build a wheel per platform** (cibuildwheel: manylinux + macOS x86_64/arm64) that compiles the `.so`
     at build time and bundles it. This is the python-chess-clean end state for the *user*, at the cost of
     CI build machinery for *us*.
   - **Graceful degradation (already in place).** If no `.so` can be built, the backends fall back
     `inproc → native → interpreter`. So even an incomplete package *runs*; it's just slower. Worth keeping
     as the floor regardless of which option above is chosen.

## Suggested order

1. `importlib.resources` for the data paths (kills the cwd assumption) — small, unblocks "run from anywhere".
2. Move the engine modules into the package (or split a `witchcraft-engine` dist) — mechanical, larger diff.
3. Decide native strategy (prebuilt `.so` vs cibuildwheel) — the real packaging investigation, and the one
   the user explicitly deferred ("whether we have the `.so` file prebuilt and shipped or a part of the build
   wheel can be investigated later").

Until then: the package is import-clean and the API is stable; **run from the repo root**.

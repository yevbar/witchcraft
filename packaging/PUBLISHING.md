# Publishing `python-mtg`

`python-mtg` ships as **self-contained binary wheels**: each wheel bundles the Datalog build, the oracle
corpus, and a per-platform compiled souffle engine `.so`, so `pip install python-mtg` works with no souffle
and no C++ toolchain on the user's machine (the numpy/PyTorch model).

The engine is the only non-portable piece, so CI builds one wheel per platform. **v1 targets:** macOS arm64,
macOS x86_64, Linux x86_64 (manylinux). Linux aarch64 / Windows are deferred.

## How a wheel is assembled (`packaging/prepare_wheel.py`)

1. Copy `datalog/*.dl` → `mtg/_datalog/` and `oracle_corpus.json` → `mtg/_data/` (bundled data).
2. Compile the in-process engine `.so` for the current platform → `mtg/_native/<tag>/libmtg_engine.so`,
   from the **same** `engine_rules.dl` that gets bundled (so the ProgramFactory-name check matches at load).
3. `pip wheel packages/mtg` picks these up via `package-data`; the wheel is retagged to the platform.

At runtime `mtg._paths` / `mtg._native` resolve these bundled paths (env override → bundled → source repo).

## One-time setup

1. **PyPI token** — create a project-scoped API token for `python-mtg` at <https://pypi.org/manage/account/token/>
   and add it as the repo secret **`PYPI_API_TOKEN`** (Settings → Secrets → Actions).
   *(Prefer Trusted Publishing/OIDC? See the comment in `.github/workflows/release.yml` — no stored secret.)*
2. **`pypi` environment** — Settings → Environments → create `pypi` (optionally require a reviewer to gate
   publishes).
3. **Data release** — the two big data files are gitignored, so CI downloads them from a rolling GitHub
   Release named **`data`**. Create it once from a checkout that has the data built:
   ```bash
   bash packaging/make_data_bundle.sh --publish     # tars cards.dl + oracle_corpus.json, uploads the asset
   ```
   Re-run this whenever the card data changes.

## Cutting a release

1. Bump `version` in `packages/mtg/pyproject.toml`.
2. If the card data changed, refresh the data bundle: `bash packaging/make_data_bundle.sh --publish`.
3. Tag and push:
   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```
   The `release.yml` workflow builds all platform wheels, smoke-tests each in a clean venv, and publishes.

## Dry run (build without publishing)

Actions → **build & publish python-mtg** → **Run workflow**, leave **publish** unticked. This builds every
platform wheel and runs the clean-venv smoke test, uploading the wheels as artifacts — but does not upload to
PyPI. Use this to validate the matrix (especially the Linux `auditwheel repair` step) before the first real tag.

## Known risks / notes

- **Linux `auditwheel repair`** on a `py3-none` (ctypes, non-CPython-extension) wheel is the least-tested
  leg — validate it with a dry run first. The `.so` links only libstdc++/libm/glibc, so there's little to
  vendor. Built on ubuntu-22.04 → the manylinux floor is glibc ≈ 2.34 (modern distros).
- **No sdist is published.** On an uncovered platform `pip install` fails clearly ("no matching wheel")
  rather than installing a broken source build. Build-on-install as a fallback is a possible follow-up.
- `mtg.analysis` (the card-text tooling) is **not** in the wheel — it needs the separate `interpreter`
  package. The shipped surface is the engine + the python-chess-style API.

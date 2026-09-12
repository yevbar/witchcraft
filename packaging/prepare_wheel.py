#!/usr/bin/env python3
"""prepare_wheel.py — stage the runtime DATA + a compiled engine .so INTO packages/mtg/ so that
`pip wheel packages/mtg` produces a self-contained wheel that works with zero setup: no repo, no souffle,
no C++ toolchain on the user's machine (the numpy/PyTorch model — the native build happened here/in CI).

What it stages (all gitignored; see .gitignore):
  * packages/mtg/_datalog/            <- datalog/*.dl        (the compiled Datalog build the engine reads)
  * packages/mtg/_data/oracle_corpus.json  <- mtgjson/...    (card characteristics)
  * packages/mtg/_native/<tag>/libmtg_engine.so             (the in-process engine, built for THIS platform)

The .so is compiled from the SAME engine_rules.dl that gets bundled, so the ProgramFactory-name check in
mtg._native/mtg.engine_inproc matches at load time by construction (a mismatched .so is ignored + recompiled).

Run on the target platform (from the repo root):  python3 packaging/prepare_wheel.py
CI runs this once per OS/arch in the build matrix before building the wheel.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packages" / "mtg"
sys.path.insert(0, str(ROOT / "packages"))          # import mtg from the source tree


def scrub_build_output() -> None:
    """Remove stale setuptools output (build/, *.egg-info) so a rebuild can't ship a leftover artifact —
    setuptools reuses build/lib, so an old per-arch .so lingering there would end up in the new wheel."""
    for junk in [PKG / "build", *PKG.glob("*.egg-info")]:
        shutil.rmtree(junk, ignore_errors=True)


def stage_data() -> None:
    """Copy the Datalog build + the oracle corpus into the package."""
    manifest = ROOT / "datalog" / "rules_version.json"
    metadata = json.loads(manifest.read_text())
    for name, key in (("rules.txt", "source_sha256"), ("datalog/engine_rules.dl", "engine_sha256")):
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != metadata[key]:
            raise RuntimeError(f"Stale generated rules: {name}; run build.py before staging")
    dl_dst = PKG / "_datalog"
    shutil.rmtree(dl_dst, ignore_errors=True)
    dl_dst.mkdir(parents=True)
    n = 0
    for f in sorted((ROOT / "datalog").glob("*.dl")):
        shutil.copy2(f, dl_dst / f.name)
        n += 1
    shutil.copy2(manifest, dl_dst / manifest.name)
    print(f"  staged {n} datalog files -> {dl_dst.relative_to(ROOT)}")

    data_dst = PKG / "_data"
    data_dst.mkdir(parents=True, exist_ok=True)
    corpus = ROOT / "mtgjson" / "oracle_corpus.json"
    shutil.copy2(corpus, data_dst / "oracle_corpus.json")
    mb = (data_dst / "oracle_corpus.json").stat().st_size / 1e6
    print(f"  staged oracle_corpus.json ({mb:.1f} MB) -> {data_dst.relative_to(ROOT)}")


def build_engine_so() -> Path:
    """Compile the in-process engine .so and place it in the package, using the same recipe
    mtg.engine_inproc uses at runtime (souffle codegen -> clang/g++ embedded shared lib).

    macOS: build a UNIVERSAL2 (arm64 + x86_64) .so on whichever Mac runs this and place it UNTAGGED
    (mtg/_native/libmtg_engine.so) — mtg._native's resolver falls back to the untagged path, so ONE fat
    library serves both Apple Silicon and Intel from a single `macosx_*_universal2` wheel (no Intel runner).
    Elsewhere (Linux): a single-arch .so under the platform-tag dir (mtg/_native/<tag>/)."""
    from mtg import _native, _paths, engine_inproc, engine_native

    rules = _paths.datalog("engine_rules.dl").read_text()     # reads the just-staged bundled copy
    edb = engine_native._edb(rules)
    src = engine_native._wrapper(rules, edb)
    name = f"mtg_inproc_{hashlib.sha1((src + engine_inproc._SHIM_CPP).encode()).hexdigest()[:12]}"

    shutil.rmtree(PKG / "_native", ignore_errors=True)        # drop any stale (single-arch/dev) .so first
    universal = sys.platform == "darwin"
    if universal:
        os.environ["MTG_SO_ARCHFLAGS"] = "-arch arm64 -arch x86_64"
        out = PKG / "_native" / "libmtg_engine.so"            # untagged -> found on both mac arches
    else:
        out = PKG / "_native" / _native.platform_tag() / "libmtg_engine.so"
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as d:
        dl, gen = Path(d) / f"{name}.dl", Path(d) / f"{name}.cpp"
        dl.write_text(src)
        g = subprocess.run([engine_native._souffle_bin(), str(dl), "-g", str(gen)],
                           capture_output=True, text=True)
        if g.returncode != 0 or not gen.exists():
            raise SystemExit(f"souffle codegen failed:\n{g.stderr}")
        if not engine_inproc._compile_lib(name, gen, out):
            raise SystemExit("C++ compile of the engine .so failed (see compiler output above)")
    kind = "universal2" if universal else _native.platform_tag()
    print(f"  built engine .so ({out.stat().st_size / 1e6:.1f} MB, {kind}, factory {name}) -> "
          f"{out.relative_to(ROOT)}")
    return out


def verify(out_so: Path) -> None:
    """Load the freshly-built .so through the runtime resolver and run one engine evaluation, so a broken
    stage fails HERE, not in a user's `pip install`."""
    import os
    os.environ["MTG_ENGINE_SO"] = str(out_so)
    for m in ("mtg", "mtg.engine_inproc", "mtg._native"):     # drop any import cached before staging
        sys.modules.pop(m, None)
    from mtg import engine_inproc
    if not engine_inproc.available():
        raise SystemExit("verify: the staged .so did not load/validate")
    print(f"  verify: engine_inproc loads the staged .so OK")


def main() -> None:
    print("staging wheel data + engine .so for", end=" ")
    from mtg import _native
    print(_native.platform_tag())
    scrub_build_output()
    stage_data()
    out = build_engine_so()
    verify(out)
    print("done — now build with:  pip wheel packages/mtg -w dist  (or python -m build packages/mtg)")


if __name__ == "__main__":
    main()

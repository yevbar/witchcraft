"""engine_native.py — drive the COMPILED souffle engine, the way PyTorch drives libtorch.

driver.py evaluates a game state by running the souffle *interpreter* over datalog/engine_rules.dl once
per distinct state — re-parsing and re-planning all 440 lines every call. This module compiles that same
Datalog ONCE to a native C++ binary (souffle's codegen -> clang/g++), then a thin shim marshals a state
into .facts files, execs the binary, and reads the derived relations back. Byte-identical to the
interpreter, ~17x faster per evaluation.

The contract mirrors driver._evaluate: in = a frozenset of (relation, frozenset(rows)); out = {relation:
set(tuples)} for every derived relation. The base (EDB) relations — those declared but never a rule head
— are wired as souffle `.input` directives reading per-state .facts files.

Graceful degradation (agent-first): build() returns None if the local toolchain can't produce a binary
(e.g. a broken Homebrew souffle), and the caller falls back to the interpreter. Nothing here is required
for correctness — only speed.
"""

from __future__ import annotations

import csv
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_SRC = Path("datalog/engine_rules.dl")
_CACHE_DIR = Path(tempfile.gettempdir())

# resolved once: (binary_path, edb_relations) or (None, None) if the toolchain can't build it.
_BUILD: tuple | None = None
# per-process working dir (fact_dir, out_dir) with EDB .facts pre-staged empty, reused across calls.
_WORK: tuple | None = None


def _edb(rules: str) -> list[str]:
    """The EDB: relations declared but never a rule head — exactly the facts the driver supplies."""
    decls = set(re.findall(r"^\.decl\s+(\w+)", rules, re.M))
    heads = set(re.findall(r"^(\w+)\([^)]*\)\s*:-", rules, re.M))
    return sorted(decls - heads)


def _wrapper(rules: str, edb: list[str]) -> str:
    """engine_rules.dl + a `.input` directive per EDB relation (read a per-state <rel>.facts file)."""
    return rules + "\n" + "\n".join(f".input {r}" for r in edb) + "\n"


def _souffle_include() -> Path | None:
    """A de-duplicated souffle header root: a temp `inc/souffle/` of symlinks to the real headers,
    minus the nested `souffle/souffle/` copy some installs ship (which collides on re-include)."""
    hit = next((p for p in (
        Path("/opt/homebrew/opt/souffle/include"),
        Path("/usr/local/include"), Path("/usr/include"),
    ) if (p / "souffle" / "CompiledSouffle.h").exists()), None)
    if hit is None:
        return None
    inc = _CACHE_DIR / "mtg_souffle_inc"
    if not (inc / "souffle" / "CompiledSouffle.h").exists():
        shutil.rmtree(inc, ignore_errors=True)
        (inc / "souffle").mkdir(parents=True)
        for e in (hit / "souffle").iterdir():
            if e.name != "souffle":                      # drop the nested duplicate tree
                (inc / "souffle" / e.name).symlink_to(e)
    return inc


def _compile(dl: Path, binp: Path) -> bool:
    """Compile a .dl to the native binary `binp`. Try souffle's own `-o` (works where compiled mode is
    healthy, e.g. Linux); fall back to manual codegen + clang/g++ for installs whose `-o` is broken."""
    # 1) the standard path: souffle generates C++ and builds the binary itself.
    r = subprocess.run(["souffle", str(dl), "-o", str(binp)], capture_output=True, text=True)
    if r.returncode == 0 and binp.exists():
        return True
    # 2) fallback: generate C++ ourselves and compile it with a sane include order.
    inc = _souffle_include()
    cxx = shutil.which("clang++") or shutil.which("g++")
    if inc is None or cxx is None:
        return False
    cpp = binp.with_suffix(".cpp")
    g = subprocess.run(["souffle", str(dl), "-g", str(cpp)], capture_output=True, text=True)
    if g.returncode != 0:
        return False
    cmd = [cxx, "-O2", "-std=c++17", f"-isystem{inc}", "-Wno-everything", str(cpp), "-o", str(binp)]
    if platform.system() == "Darwin":                    # match the active SDK, not whatever brew baked in
        sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        if sdk:
            cmd.insert(1, f"-isysroot{sdk}")
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0 and binp.exists()


def build() -> tuple:
    """Compile engine_rules.dl to a native binary (cached by content hash); returns (path, edb) or
    (None, None) if the toolchain can't build one. Idempotent — safe to call on every evaluate()."""
    global _BUILD
    if _BUILD is not None:
        return _BUILD
    rules = _SRC.read_text()
    edb = _edb(rules)
    src = _wrapper(rules, edb)
    binp = _CACHE_DIR / f"mtg_engine_{hashlib.sha1(src.encode()).hexdigest()[:12]}"
    if not binp.exists():
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / "engine.dl"
            dl.write_text(src)
            if not _compile(dl, binp):
                _BUILD = (None, None)
                return _BUILD
    _BUILD = (binp, edb)
    return _BUILD


def available() -> bool:
    return build()[0] is not None


def _workdir(edb: list[str]) -> tuple:
    """A per-process working dir with every EDB <rel>.facts pre-staged empty (souffle errors on a
    missing input file). Reused across calls so each evaluate writes only the non-empty relations
    instead of 65 files + a fresh tempdir — ~1.8x less marshalling. Keyed by pid so forked simulation
    workers never share a dir; the driver is single-threaded within a process."""
    global _WORK
    if _WORK is None:
        d = _CACHE_DIR / f"mtg_engine_work_{os.getpid()}"
        fd, od = d / "facts", d / "out"
        shutil.rmtree(d, ignore_errors=True)
        fd.mkdir(parents=True)
        od.mkdir()
        for rel in edb:
            (fd / f"{rel}.facts").write_text("")
        _WORK = (fd, od)
    return _WORK


def evaluate(fkey: frozenset) -> dict:
    """Run the compiled engine on a fact set; return {relation: set(tuples)} for every output relation.
    Same signature/return as driver._evaluate. Raises if no binary is available (guard with available())."""
    binp, edb = build()
    if binp is None:
        raise RuntimeError("native engine binary unavailable")
    fd, od = _workdir(edb)
    nonempty = [(rel, rows) for rel, rows in fkey if rows]
    for rel, rows in nonempty:                           # souffle reads TSV (no quotes); ints/symbols as-is
        (fd / f"{rel}.facts").write_text("".join("\t".join(map(str, row)) + "\n" for row in rows))
    try:
        subprocess.run([str(binp), "-F", str(fd), "-D", str(od)], check=True, capture_output=True)
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}
                for f in od.glob("*.csv")}
    finally:
        for rel, _ in nonempty:                          # reset to empty for the next state
            (fd / f"{rel}.facts").write_text("")


if __name__ == "__main__":
    binp, edb = build()
    print(f"native engine: {'built ' + str(binp) if binp else 'UNAVAILABLE (interpreter fallback)'}")
    if binp:
        print(f"  {len(edb)} EDB (input) relations wired as .facts")

"""engine_inproc.py — the COMPILED souffle engine called IN-PROCESS (no fork, no files), the way PyTorch
calls libtorch rather than shelling out.

engine_native.py already compiles datalog/engine_rules.dl to a native binary, but every evaluate() still
forks that binary and round-trips a state through `.facts`/`.csv` files in /tmp. The sim-throughput benchmark
measured that as ~75% overhead: of ~7.3 ms/state, only ~1.9 ms is the Soufflé fixpoint — the rest is ~1.7 ms
process spawn + ~3.6 ms filesystem I/O. This module removes BOTH: it links the souffle-generated C++ as a
shared library and calls it through a tiny `extern "C"` shim over ctypes, passing facts/relations as in-memory
TSV blobs. Same contract and byte-identical results as engine_native.evaluate (it reuses the SAME wrapped
program, so the derivations are identical) — just no subprocess and no /tmp.

Graceful degradation (agent-first): build() returns False if the local toolchain can't produce the .so (e.g.
no souffle headers / no C++ codegen), and the caller falls back to engine_native (subprocess) and then the
interpreter. Nothing here is required for correctness — only speed.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from mtg import engine_native
from mtg import _native

_CACHE_DIR = Path(tempfile.gettempdir())


def _load_lib(lib: Path, name: str):
    """ctypes-load the engine .so, bind the shim signatures, and validate that its ProgramFactory matches
    `name` (so a hand-placed/shipped .so built from a different engine_rules.dl is rejected, not silently
    wrong). Returns the loaded CDLL, or None if it can't load or the factory name doesn't match."""
    try:
        cdll = ctypes.CDLL(str(lib))
        cdll.mtg_create.restype = ctypes.c_void_p
        cdll.mtg_create.argtypes = [ctypes.c_char_p]
        cdll.mtg_run.restype = ctypes.c_void_p                   # void* so we can free the exact pointer
        cdll.mtg_run.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.mtg_run_delta.restype = ctypes.c_void_p
        cdll.mtg_run_delta.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        cdll.mtg_destroy.argtypes = [ctypes.c_void_p]
        cdll.mtg_free.argtypes = [ctypes.c_void_p]
    except OSError:
        return None
    h = cdll.mtg_create(name.encode())                          # validate the factory is registered under `name`
    if not h:
        return None                                            # mismatched/empty .so -> caller falls back
    cdll.mtg_destroy(h)
    return cdll

# The C ABI shim: a live program instance.
#  mtg_run(h, facts)              — FULL load: purge everything, insert all input facts, run, serialize.
#  mtg_run_delta(h, purge, facts) — DELTA load (lever #2/#3): purge only the named (changed/removed) input
#                                   relations, (re)insert `facts` (the changed relations' rows), then purge
#                                   internal+output and run the FULL fixpoint over the patched inputs. Result
#                                   is byte-identical to a full load (run() recomputes from current inputs) —
#                                   this is "incremental INPUT, full recompute": it skips re-marshalling the
#                                   ~unchanged input (consecutive states differ by ~2 facts of ~485), NOT the
#                                   derivation. Correct by construction; the caller tracks what's loaded.
_SHIM_CPP = r"""
#include "souffle/SouffleInterface.h"
#include <string>
#include <cstdlib>
#include <cstring>
using namespace souffle;

static void insert_blob(SouffleProgram* p, const char* facts) {
    std::string blob(facts);
    size_t i = 0, n = blob.size();
    while (i < n) {
        size_t nl = blob.find('\n', i);
        if (nl == std::string::npos) nl = n;
        if (nl > i) {
            std::string line = blob.substr(i, nl - i);
            size_t t0 = line.find('\t');
            std::string rel = (t0 == std::string::npos) ? line : line.substr(0, t0);
            Relation* r = p->getRelation(rel);
            if (r != nullptr) {
                size_t arity = r->getArity();
                tuple tup(r);
                size_t pos = (t0 == std::string::npos) ? line.size() : t0 + 1;
                size_t fi = 0;
                bool ok = true;
                for (; fi < arity; fi++) {
                    if (pos > line.size()) { ok = false; break; }
                    size_t nt = line.find('\t', pos);
                    size_t end = (nt == std::string::npos) ? line.size() : nt;
                    std::string field = line.substr(pos, end - pos);
                    pos = end + 1;
                    char ty = *r->getAttrType(fi);
                    if (ty == 's') {
                        tup << field;
                    } else {
                        try { tup << (RamSigned) std::stoll(field); }
                        catch (...) { ok = false; break; }      // a non-numeric in a number column -> skip the fact
                    }
                }
                if (ok && fi == arity) r->insert(tup);
            }
        }
        i = nl + 1;
    }
}

static char* serialize_outputs(SouffleProgram* p) {
    std::string out;
    for (Relation* r : p->getOutputRelations()) {
        if (r->size() == 0) continue;                            // skip empty (matches the .csv-skip behavior)
        const std::string name = r->getName();
        size_t arity = r->getArity();
        for (auto& tup : *r) {
            out += name;
            for (size_t k = 0; k < arity; k++) {
                out += '\t';
                char ty = *r->getAttrType(k);
                RamDomain v = tup[k];
                if (ty == 's') out += r->getSymbolTable().decode(v);
                else out += std::to_string(v);
            }
            out += '\n';
        }
    }
    char* res = (char*) malloc(out.size() + 1);
    memcpy(res, out.data(), out.size());
    res[out.size()] = '\0';
    return res;
}

extern "C" {

void* mtg_create(const char* name) {
    return (void*) ProgramFactory::newInstance(std::string(name));
}
void mtg_destroy(void* h) { delete (SouffleProgram*) h; }
void mtg_free(char* s) { free(s); }

char* mtg_run(void* h, const char* facts) {
    SouffleProgram* p = (SouffleProgram*) h;
    p->purgeInputRelations();
    p->purgeInternalRelations();
    p->purgeOutputRelations();
    insert_blob(p, facts);
    p->run();
    return serialize_outputs(p);
}

char* mtg_run_delta(void* h, const char* purge, const char* facts) {
    SouffleProgram* p = (SouffleProgram*) h;
    std::string pb(purge);                                       // newline-separated input relations to clear
    size_t i = 0, n = pb.size();
    while (i < n) {
        size_t nl = pb.find('\n', i);
        if (nl == std::string::npos) nl = n;
        if (nl > i) {
            Relation* r = p->getRelation(pb.substr(i, nl - i));
            if (r != nullptr) r->purge();
        }
        i = nl + 1;
    }
    // ALL purges BEFORE the insert: purgeOutput would otherwise wipe just-inserted echoed-input relations
    // (those declared both .input and .output, e.g. printed_color/printed_keyword). After this the only facts
    // present are the carried-over keepable inputs; insert the (re)loaded relations, then recompute the fixpoint.
    p->purgeInternalRelations();
    p->purgeOutputRelations();
    insert_blob(p, facts);                                       // (re)insert the changed + always-reload rows
    p->run();
    return serialize_outputs(p);
}

}  // extern "C"
"""

# resolved once: (lib, program_name, edb) or None if it can't be built/loaded.
_LIB: tuple | None = None
_HANDLE = None                                                    # the live SouffleProgram* (reused across calls)
_FAILED = False
_LOADED: dict | None = None                                       # {rel: frozenset(rows)} currently in the instance
_NONKEEP: frozenset = frozenset()                                 # inputs that must be purged+reinserted every call
_NO_DELTA = bool(os.environ.get("MTG_NO_DELTA"))                  # force full loads (A/B + falsifiability escape)


def _compile_lib(name: str, gen_cpp: Path, lib: Path) -> bool:
    """Compile the souffle-generated C++ + the shim into a shared library (embedded, no main). Mirrors
    engine_native._compile's flags (the de-duped include root, -w to silence warnings) plus -fPIC -shared and
    -D__EMBEDDED_SOUFFLE__ (drops the generated main(), registers the ProgramFactory)."""
    inc = engine_native._souffle_include()
    if inc is None:
        return False
    import shutil
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        return False
    shim = gen_cpp.with_name("mtg_inproc_shim.cpp")
    shim.write_text(_SHIM_CPP)
    cmd = [cxx, "-O2", "-std=c++17", "-fPIC", "-shared", "-D__EMBEDDED_SOUFFLE__",
           f"-isystem{inc}", "-w", "-pthread",   # -w (not clang-only -Wno-everything) so GCC also stays quiet
           str(gen_cpp), str(shim), "-o", str(lib)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and lib.exists()


def build() -> tuple | None:
    """Generate + compile the in-process engine .so (cached by content hash); returns (lib, name, edb) or
    None if unbuildable. Idempotent."""
    global _LIB, _FAILED
    if _LIB is not None or _FAILED:
        return _LIB
    global _NONKEEP
    rules = engine_native._SRC.read_text()
    edb = engine_native._edb(rules)
    # NONKEEP = input relations the delta path must purge+reinsert EVERY call (can't carry across states):
    #   * input ∩ rule-head (the SHIM_INPUTS .input+rule union, e.g. loses_abilities/goaded) — run() derives
    #     extra facts INTO them and purgeInternalRelations() won't clear them (souffle marks them input);
    #   * input ∩ .output (echoed inputs, e.g. printed_color/printed_keyword) — purgeOutputRelations() clears
    #     them, so a carried-over copy would be wiped before run().
    # The other ~130 inputs are pure (input-only, not head/output) — they hold exactly what we insert, so an
    # UNCHANGED one is safe to keep loaded across the delta (the whole point: skip re-marshalling ~70% of facts).
    import re as _re
    _heads = set(_re.findall(r"^(\w+)\([^)]*\)\s*:-", rules, _re.M))
    _outs = set(_re.findall(r"^\.output\s+(\w+)", rules, _re.M))
    _NONKEEP = frozenset(set(edb) & (_heads | _outs))
    src = engine_native._wrapper(rules, edb)
    h = hashlib.sha1((src + _SHIM_CPP).encode()).hexdigest()[:12]  # shim in the hash -> a shim change rebuilds
    name = f"mtg_inproc_{h}"                                      # == the .dl stem == the ProgramFactory name

    pre = _native.engine_so()                                    # a shipped / hand-placed prebuilt .so?
    if pre is not None:
        cdll = _load_lib(pre, name)                              # use it directly if its factory matches this .dl
        if cdll is not None:
            _LIB = (cdll, name, edb)
            return _LIB
        # mismatched prebuilt (built from a different engine_rules.dl) -> fall through to compile on demand

    lib = _CACHE_DIR / f"lib{name}.so"
    if not lib.exists():
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / f"{name}.dl"
            gen = Path(d) / f"{name}.cpp"
            dl.write_text(src)
            g = subprocess.run([engine_native._souffle_bin(), str(dl), "-g", str(gen)], capture_output=True, text=True)
            if g.returncode != 0 or not gen.exists() or not _compile_lib(name, gen, lib):
                _FAILED = True
                return None
    cdll = _load_lib(lib, name)
    if cdll is None:
        _FAILED = True
        return None
    _LIB = (cdll, name, edb)
    return _LIB


def available() -> bool:
    return build() is not None


def _instance():
    global _HANDLE
    built = build()
    if built is None:
        return None
    cdll, name, _edb = built
    if _HANDLE is None:
        _HANDLE = cdll.mtg_create(name.encode())
        if not _HANDLE:
            return None
    return _HANDLE


def _rel_blob(items) -> bytes:
    """TSV-marshal an iterable of (rel, rows) (souffle reads symbols/ints as text — stringify like the .facts
    path). One line per row: 'rel\\tf1\\tf2...'."""
    parts = [rel + "\t" + "\t".join(map(str, row)) for rel, rows in items if rows for row in rows]
    return ("\n".join(parts) + "\n").encode() if parts else b""


def reset() -> None:
    """Forget the carried-over input state so the NEXT evaluate() does a FULL load. The delta path keeps the
    instance's inputs loaded and patches only what changed between consecutive (forward-evolving) states — but
    when the next call is an INDEPENDENT scenario (a fresh state that happens to reuse instance ids), the carried
    `_LOADED` is a wrong baseline to diff against. `driver.clear_cache()` calls this at scenario boundaries so an
    independent run is isolated; within a game the carry-over stays (and is byte-identical to a full load)."""
    global _LOADED
    _LOADED = None


def evaluate(fkey: frozenset) -> dict:
    """Run the compiled engine IN-PROCESS on a fact set; return {relation: set(tuples)} for every non-empty
    output relation — same signature/return as engine_native.evaluate and driver._evaluate. Raises if the
    library is unavailable (guard with available()).

    DELTA-INPUT (lever #2/#3): consecutive states differ by ~2 facts of ~485, so instead of re-marshalling +
    re-inserting the whole state every call, we keep the instance's input relations loaded and patch only the
    relations whose row-set changed (purge+reinsert) or vanished (purge). Then the C side recomputes the FULL
    fixpoint over the patched inputs — byte-identical to a full load (verified in test_engine_native), just
    without the input-marshalling cost for the unchanged ~95% of the state. MTG_NO_DELTA forces full loads."""
    global _LOADED
    built = build()
    if built is None:
        raise RuntimeError("in-process engine library unavailable")
    cdll, _name, _edb = built
    handle = _instance()
    if handle is None:
        raise RuntimeError("in-process engine instance unavailable")

    new = {rel: rows for rel, rows in fkey if rows}
    if _NO_DELTA or _LOADED is None:
        ptr = cdll.mtg_run(handle, _rel_blob(new.items()))       # FULL load (first call / forced)
    else:
        # purge every relation whose INPUT changed/vanished, PLUS all NONKEEP relations (run() pollutes those
        # or purgeOutput clears them — they can't carry over); reinsert the input facts for whatever we purged
        # that still has rows. The kept (unchanged, pure-input) relations carry over untouched.
        to_purge = {rel for rel, rows in new.items() if _LOADED.get(rel) != rows}
        to_purge |= {rel for rel in _LOADED if rel not in new}
        to_purge |= _NONKEEP
        purge = ("\n".join(to_purge) + "\n").encode() if to_purge else b""
        facts = _rel_blob((rel, new[rel]) for rel in to_purge if rel in new)
        ptr = cdll.mtg_run_delta(handle, purge, facts)
    _LOADED = new                                                # the instance's inputs now equal `new`
    try:
        text = ctypes.string_at(ptr).decode()
    finally:
        cdll.mtg_free(ptr)
    out: dict = {}
    for line in text.split("\n"):
        if not line:
            continue
        cells = line.split("\t")
        out.setdefault(cells[0], set()).add(tuple(cells[1:]))
    return out


if __name__ == "__main__":
    b = build()
    print(f"in-process engine: {'built ' + b[1] if b else 'UNAVAILABLE (subprocess/interpreter fallback)'}")
    if b:
        print(f"  {len(b[2])} EDB (input) relations; lib={_CACHE_DIR}/lib{b[1]}.so")

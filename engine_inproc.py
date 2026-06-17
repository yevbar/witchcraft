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
import subprocess
import tempfile
from pathlib import Path

import engine_native  # reuse _SRC / _edb / _wrapper / _souffle_include so the wrapped program is IDENTICAL

_CACHE_DIR = Path(tempfile.gettempdir())

# The C ABI shim: create a program instance by factory name, run one state from a TSV fact blob (purging the
# prior state first), serialize the non-empty output relations back as a TSV blob. All in-memory.
_SHIM_CPP = r"""
#include "souffle/SouffleInterface.h"
#include <string>
#include <cstdlib>
#include <cstring>
using namespace souffle;

extern "C" {

void* mtg_create(const char* name) {
    return (void*) ProgramFactory::newInstance(std::string(name));
}
void mtg_destroy(void* h) { delete (SouffleProgram*) h; }
void mtg_free(char* s) { free(s); }

// facts: lines "relname\tf1\tf2...\n" (only non-empty input relations need appear). Returns a malloc'd C
// string of the derived non-empty output relations in the same TSV line format. Caller frees via mtg_free.
char* mtg_run(void* h, const char* facts) {
    SouffleProgram* p = (SouffleProgram*) h;
    p->purgeInputRelations();
    p->purgeInternalRelations();
    p->purgeOutputRelations();

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

    p->run();

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

}  // extern "C"
"""

# resolved once: (lib, program_name, edb) or None if it can't be built/loaded.
_LIB: tuple | None = None
_HANDLE = None                                                    # the live SouffleProgram* (reused across calls)
_FAILED = False


def _compile_lib(name: str, gen_cpp: Path, lib: Path) -> bool:
    """Compile the souffle-generated C++ + the shim into a shared library (embedded, no main). Mirrors
    engine_native._compile's flags (the de-duped include root, -Wno-everything) plus -fPIC -shared and
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
           f"-isystem{inc}", "-Wno-everything", "-pthread",
           str(gen_cpp), str(shim), "-o", str(lib)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and lib.exists()


def build() -> tuple | None:
    """Generate + compile the in-process engine .so (cached by content hash); returns (lib, name, edb) or
    None if unbuildable. Idempotent."""
    global _LIB, _FAILED
    if _LIB is not None or _FAILED:
        return _LIB
    rules = engine_native._SRC.read_text()
    edb = engine_native._edb(rules)
    src = engine_native._wrapper(rules, edb)
    h = hashlib.sha1(src.encode()).hexdigest()[:12]
    name = f"mtg_inproc_{h}"                                      # == the .dl stem == the ProgramFactory name
    lib = _CACHE_DIR / f"lib{name}.so"
    if not lib.exists():
        with tempfile.TemporaryDirectory() as d:
            dl = Path(d) / f"{name}.dl"
            gen = Path(d) / f"{name}.cpp"
            dl.write_text(src)
            g = subprocess.run(["souffle", str(dl), "-g", str(gen)], capture_output=True, text=True)
            if g.returncode != 0 or not gen.exists() or not _compile_lib(name, gen, lib):
                _FAILED = True
                return None
    try:
        cdll = ctypes.CDLL(str(lib))
        cdll.mtg_create.restype = ctypes.c_void_p
        cdll.mtg_create.argtypes = [ctypes.c_char_p]
        cdll.mtg_run.restype = ctypes.c_void_p                   # void* so we can free the exact pointer
        cdll.mtg_run.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.mtg_destroy.argtypes = [ctypes.c_void_p]
        cdll.mtg_free.argtypes = [ctypes.c_void_p]
    except OSError:
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


def evaluate(fkey: frozenset) -> dict:
    """Run the compiled engine IN-PROCESS on a fact set; return {relation: set(tuples)} for every non-empty
    output relation — same signature/return as engine_native.evaluate and driver._evaluate. Raises if the
    library is unavailable (guard with available())."""
    built = build()
    if built is None:
        raise RuntimeError("in-process engine library unavailable")
    cdll, _name, _edb = built
    handle = _instance()
    if handle is None:
        raise RuntimeError("in-process engine instance unavailable")
    # marshal the non-empty input relations into one TSV blob (souffle reads symbols/ints as text, same as
    # the .facts path — we stringify exactly like engine_native).
    parts = []
    for rel, rows in fkey:
        if not rows:
            continue
        for row in rows:
            parts.append(rel + "\t" + "\t".join(map(str, row)))
    blob = ("\n".join(parts) + "\n").encode() if parts else b""
    ptr = cdll.mtg_run(handle, blob)
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

"""harness.py — drive a souffle program compiled with the fork's --incremental, IN-PROCESS, for Phase 3.

Phase 3's `update` subroutine is stateful: Bootstrap once, then call update with an input diff, then read the
resident relations. That can't be tested by a stateless CLI run, so this harness keeps a live SouffleProgram
instance and exposes exactly the operations the three-term Update needs:

    h = Harness(dl_text)          # compile <dl> with the fork's `souffle --incremental`, link the shim, load
    h.bootstrap(facts)            # full from-scratch evaluation over `facts`  (dict: rel -> set(rows))
    h.insert({"diff_plus_edge": {("9","9")}})   # stage tuples into named relations (no purge)
    h.update()                    # executeSubroutine("update")  (once it exists)
    h.dump()                      # {rel: set(rows)} for EVERY relation (data columns only)
    h.dump(["path"])              # a subset

It uses the FORK binary (third_party/souffle/build/src/souffle) and the FORK headers, so the @count/@iteration
columns and the `update` subroutine are present. Caches the .so by content hash. Pure test rig — nothing in the
shipped engine depends on it.
"""

from __future__ import annotations

import ctypes
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
_SOUFFLE = _ROOT / "third_party" / "souffle" / "build" / "src" / "souffle"
_INCLUDE = _ROOT / "third_party" / "souffle" / "src" / "include"
_SHIM = _HERE / "shim.cpp"
_CACHE = Path(tempfile.gettempdir())


def _lit(v) -> str:
    return str(v)


def _blob(items) -> bytes:
    """TSV-marshal {rel: rows} -> b'rel\\tf1\\tf2\\n...'."""
    parts = [rel + "\t" + "\t".join(map(_lit, row)) for rel, rows in items for row in rows]
    return ("\n".join(parts) + "\n").encode() if parts else b""


def _parse(text: str) -> dict:
    out: dict = {}
    for line in text.split("\n"):
        if not line:
            continue
        cells = line.split("\t")
        out.setdefault(cells[0], set()).add(tuple(cells[1:]))
    return out


def available() -> bool:
    return _SOUFFLE.exists() and (shutil.which("g++") or shutil.which("clang++")) is not None


class Harness:
    def __init__(self, dl_text: str, incremental: bool = True):
        self.dl_text = dl_text
        self.incremental = incremental
        self._cdll = None
        self._h = None
        self._build()
        self._instantiate()

    def _build(self):
        flag = "incr" if self.incremental else "plain"
        # Include the souffle binary's mtime so rebuilding the fork invalidates cached .so's (the generated
        # code changes even when the .dl/shim do not — e.g. when the `update` subroutine is added).
        sversion = str(_SOUFFLE.stat().st_mtime_ns) if _SOUFFLE.exists() else "0"
        digest = hashlib.sha1((self.dl_text + _SHIM.read_text() + flag + sversion).encode()).hexdigest()[:12]
        name = f"hns_{digest}"
        self._name = name
        lib = _CACHE / f"lib{name}.so"
        if not lib.exists():
            with tempfile.TemporaryDirectory() as d:
                dl = Path(d) / f"{name}.dl"
                gen = Path(d) / f"{name}.cpp"
                dl.write_text(self.dl_text)
                args = [str(_SOUFFLE)] + (["--incremental"] if self.incremental else []) + [
                    str(dl), "-g", str(gen)]
                g = subprocess.run(args, capture_output=True, text=True)
                if g.returncode != 0 or not gen.exists():
                    raise RuntimeError(f"souffle codegen failed:\n{g.stderr}")
                cxx = shutil.which("g++") or shutil.which("clang++")
                cmd = [cxx, "-O2", "-std=c++17", "-fPIC", "-shared", "-D__EMBEDDED_SOUFFLE__",
                       f"-isystem{_INCLUDE}", "-Wno-everything", "-pthread",
                       str(gen), str(_SHIM), "-o", str(lib)]
                c = subprocess.run(cmd, capture_output=True, text=True)
                if c.returncode != 0 or not lib.exists():
                    raise RuntimeError(f"shim compile failed:\n{c.stderr}")
        self._lib_path = lib

    def _instantiate(self):
        cdll = ctypes.CDLL(str(self._lib_path))
        cdll.h_create.restype = ctypes.c_void_p
        cdll.h_create.argtypes = [ctypes.c_char_p]
        cdll.h_destroy.argtypes = [ctypes.c_void_p]
        cdll.h_free.argtypes = [ctypes.c_void_p]
        cdll.h_bootstrap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.h_insert.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.h_purge.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.h_subroutine.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        cdll.h_dump_all.restype = ctypes.c_void_p
        cdll.h_dump_all.argtypes = [ctypes.c_void_p]
        cdll.h_dump.restype = ctypes.c_void_p
        cdll.h_dump.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._cdll = cdll
        self._h = cdll.h_create(self._name.encode())
        if not self._h:
            raise RuntimeError("failed to instantiate program")

    def bootstrap(self, facts: dict):
        self._cdll.h_bootstrap(self._h, _blob(facts.items()))

    def insert(self, facts: dict):
        self._cdll.h_insert(self._h, _blob(facts.items()))

    def purge(self, names):
        self._cdll.h_purge(self._h, ("\n".join(names) + "\n").encode())

    def update(self):
        self._cdll.h_subroutine(self._h, b"update")

    def subroutine(self, name: str):
        self._cdll.h_subroutine(self._h, name.encode())

    def _take(self, ptr) -> str:
        try:
            return ctypes.string_at(ptr).decode()
        finally:
            self._cdll.h_free(ptr)

    def dump(self, names=None) -> dict:
        if names is None:
            return _parse(self._take(self._cdll.h_dump_all(self._h)))
        return _parse(self._take(self._cdll.h_dump(self._h, ("\n".join(names) + "\n").encode())))

    def close(self):
        if self._h:
            self._cdll.h_destroy(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

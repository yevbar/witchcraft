"""test_forge_discovery.py — Forge local-environment auto-discovery (forge_integration/run_tournament.py)
and the forge_status() diagnostic.

forge_available() returned False on any machine but the mac mini because JDK/FATJAR defaulted to hardcoded
/home/zucc paths. These checks pin the discovery CONTRACT (env overrides win; an existing default wins; a
discovered assets dir ALWAYS ends in a separator — a missing trailing slash silently broke Forge asset
loading) without needing a JVM. No Forge install required.

Run: python3 test_forge_discovery.py
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root on sys.path (test relocated into subfolder)

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "forge_integration"))
import run_tournament as rt

from mtg import forge_status

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _without(*keys):
    """Snapshot + clear env keys; returns a restore() closure."""
    saved = {k: os.environ.pop(k, None) for k in keys}

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return restore


def _env_overrides_win():
    restore = _without("JDK", "FATJAR", "FORGE_ASSETS")
    try:
        os.environ["JDK"] = "/custom/jdk"
        os.environ["FATJAR"] = "/custom/forge.jar"
        os.environ["FORGE_ASSETS"] = "/custom/assets/"
        check("$JDK overrides discovery", rt._discover_jdk("/whatever") == "/custom/jdk")
        check("$FATJAR overrides discovery", rt._discover_fatjar("/f", "/d.jar") == "/custom/forge.jar")
        check("$FORGE_ASSETS overrides discovery", rt._discover_assets("/f", "/d.jar") == "/custom/assets/")
    finally:
        restore()


def _existing_default_wins():
    restore = _without("JDK")
    try:
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "bin"))
            open(os.path.join(d, "bin", "java"), "w").close()       # a default with a real bin/java
            check("an existing hardcoded default JDK is kept (mac-mini path stays valid there)",
                  rt._discover_jdk(d) == d)
    finally:
        restore()


def _assets_always_trailing_sep():
    restore = _without("FORGE_ASSETS")
    try:
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "res"))                     # installed-release layout: res/ next to the jar
            fatjar = os.path.join(d, "forge-gui-desktop-x-jar-with-dependencies.jar")
            assets = rt._discover_assets("/no/such/forge", fatjar)
            check("discovered assets dir ends in a separator (the trailing-slash bug)", assets.endswith(os.sep))
            check("discovered assets dir is the one holding res/", os.path.isdir(os.path.join(assets, "res")))
    finally:
        restore()


def _forge_status_shape():
    st = forge_status()
    check("forge_status() reports availability", "available" in st)
    check("forge_status() surfaces the resolved jdk + fatjar paths (debuggable when False)",
          ("error" in st) or ({"jdk", "jdk_ok", "fatjar", "fatjar_ok"} <= set(st)))


def run():
    _env_overrides_win()
    _existing_default_wins()
    _assets_always_trailing_sep()
    _forge_status_shape()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()

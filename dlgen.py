"""Tiny deterministic Datalog emitter: structured rule defs -> Datalog text.

This is the mechanical IR->Datalog half of the pipeline. It does NO interpretation
— it just lowers explicit Python structures (lists, rule tuples) into Datalog
syntax. The interpretation lives in the build_*.py files as auditable data; this
file is the small, obviously-correct transpiler.
"""

from __future__ import annotations


class Program:
    def __init__(self) -> None:
        self._out: list[str] = []

    def blank(self) -> None:
        self._out.append("")

    def comment(self, text: str) -> None:
        self._out.append(f"// {text}")

    def decl(self, name: str, cols: list[tuple[str, str]]) -> None:
        sig = ", ".join(f"{n}: {t}" for n, t in cols)
        self._out.append(f".decl {name}({sig})")

    def fact(self, atom: str) -> None:
        self._out.append(f"{atom}.")

    def facts(self, atoms: list[str]) -> None:
        """Several facts on one line (for compact fixture/data tables)."""
        self._out.append(" ".join(f"{a}." for a in atoms))

    def rule(self, head: str, body: list[str], note: str | None = None) -> None:
        if note:
            self.comment(note)
        self._out.append(f"{head} :- {', '.join(body)}.")

    def output(self, *names: str) -> None:
        for n in names:
            self._out.append(f".output {n}")

    def raw(self, line: str) -> None:
        self._out.append(line)

    def conformance(self, expect_decls, checks) -> None:
        """Emit the standard expect_*/conformance_fail harness.

        expect_decls: list of (name, cols). checks: list of
        (kind, expect_atom, polarity, target_atom) where polarity is
        "miss" (fail if target absent) or "hit" (fail if target present).
        Shared variable names link the expect and target atoms.
        """
        for name, cols in expect_decls:
            self.decl(name, cols)
        self.decl("conformance_fail", [("kind", "symbol"), ("a", "symbol"), ("b", "symbol")])
        for check in checks:
            kind, expect, polarity, target = check[:4]
            if len(check) >= 6:        # explicit symbol args to record (e.g. when an
                a, b = check[4], check[5]   # expect arg is numeric and can't be recorded)
            else:
                args = _atom_args(expect)
                a = args[0] if args else '"-"'
                b = args[1] if len(args) > 1 else '"-"'
            neg = "!" if polarity == "miss" else ""
            self.rule(f'conformance_fail("{kind}", {a}, {b})', [expect, f"{neg}{target}"])
        self.raw(".output conformance_fail")
        self.raw(".printsize conformance_fail")

    def text(self) -> str:
        return "\n".join(self._out) + "\n"


def _atom_args(atom: str) -> list[str]:
    """The argument tokens of a Datalog atom, e.g. f(A, B) -> [A, B]."""
    inner = atom[atom.index("(") + 1:atom.rindex(")")]
    return [a.strip() for a in inner.split(",")]

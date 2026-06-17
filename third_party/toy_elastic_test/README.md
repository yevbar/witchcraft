# Toy elastic sanity (Phase-A end)

A 3-rule transitive-closure program — the canonical incremental-Datalog sanity case — used to
(a) document the elastic insert/delete protocol and (b) compute the full-recompute oracle that an
elastic `update` must match byte-for-byte.

## `tc.dl`
```
.decl edge(x:number, y:number)
.input edge
.decl path(x:number, y:number)
.output path
path(x, y) :- edge(x, y).
path(x, y) :- edge(x, z), path(z, y).
```

## Oracle (computed here with the INSTALLED 2.x souffle — the elastic fork did NOT build on this box)

Base EDB `edge = {(1,2),(2,3),(2,4)}`  → `path = {(1,2),(1,3),(1,4),(2,3),(2,4)}`
After deleting `edge(2,4)`             → `path = {(1,2),(1,3),(2,3)}`

The delete drops exactly `path(1,4)` and `path(2,4)`; `path(1,3)` SURVIVES because it still has a
distinct support `edge(1,2), path(2,3)`. That multi-support retraction is the property the elastic
`update` subroutine must reproduce — and the byte-identity invariant (`delta == full`) is asserted
against precisely this kind of recompute oracle.

## How the SAME sanity would run under the elastic fork (could not execute here — see build notes)

Compile with incremental + run the generated binary, then drive the REPL on stdin:
```
souffle --incremental -c tc.dl -o tc          # NB: pre-2.0 flags; -c = compile
echo 'edge 1 2 ... ' > facts/edge.facts        # bootstrap EDB (annotated; see notes)
./tc                                            # bootstraps, then enters the incremental REPL:
> remove edge(2, 4)
> commit            # runs executeSubroutine("incremental_update_clear_diffs") then ("update")
# dump path and diff path == oracle above
```
See `../../SOUFFLE_ELASTIC_BUILD_NOTES.md` for the exact embedded (no-REPL) call sequence that
replaces the REPL — that is what `engine_inproc.mtg_run_delta` would call.

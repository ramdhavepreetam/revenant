# ADR-0002 — Monorepo package split

- **Status:** Implemented
- **Phase:** P0 (foundation) · **F-slices:** pre-F
- **Date proposed:** (backfilled 2026-07-30) · **Date implemented:** (foundational)
- **Depends on:** ADR-0001 · **Blocks:** placement rules for all later ADRs

## Context
The project began entangled with a private companion web app (AIBot). To publish
a clean public CLI while keeping a shared engine, the code had to be separated
into installable packages with a clear, enforceable dependency direction.

## Decision
Three pip-installable packages in a monorepo, with a strictly **acyclic**
dependency graph:

```
nerva-core    shared: LLM layer + memory/profiles/storage
   ↑
nerva-agent   the agent engine: tool loop, protocol, tools, routing, capacity
   ↑
revenant-cli  the `revenant` command (depends on the two above)
```

`nerva-core ← nerva-agent ← revenant-cli`. The private companion app lives in a
separate repository and is not part of this tree.

## Design detail — placement rule (load-bearing for every phase)
New code lands in the **lowest package that can hold it**:
- Pure LLM/model/storage/memory logic → `nerva-core`.
- Anything about the agent loop, tools, protocol, routing → `nerva-agent`.
- CLI, config files, subcommands, terminal UX → `revenant-cli`.

A module must never import "upward" (e.g. `nerva-agent` importing `revenant-cli`
or a web app). The router docstring enforces this by convention: "imports ONLY
local_llm_writer, never web_app."

## Consequences
- MCP client (P3): protocol/transport in `nerva-agent`, config+subcommand in
  `revenant-cli`.
- Skills loader (P4): discovery/format in `nerva-agent`; `.revenant/skills`
  resolution + `/skill` UX in `revenant-cli`.
- Graph (P7): indexer + retrieval tools in `nerva-agent`; any heavy vector/graph
  store primitives may live in `nerva-core`.

## Progress log
- 2026-07-30 — Backfilled from README layout and git history (`Restructure into a
  monorepo`, `Split out the companion app`).

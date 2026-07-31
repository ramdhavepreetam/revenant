# Revenant — Architecture Decisions & Development Roadmap

> **This is the resume-from-cold document.** If a working session is lost, start
> here. It records every architectural decision (ADRs) and the full phase-wise
> plan in enough detail to pick up implementation without re-deriving context.

**Last updated:** 2026-07-30 · **Tests:** 247 green
**Repo:** local, offline coding-agent CLI over Ollama · packages: `nerva-core ← nerva-agent ← revenant-cli`

---

## ▶ START HERE — current state (resume point)

- **Done & committed:** P0 (engine), **P2.5** undo+ADRs (branch
  `phase2.5-undo-and-adr-roadmap`, commit `7fc5fa9`), **P3** MCP (branch
  `phase3-mcp-integration`, commit `6226776`). Both branches are **committed but
  not yet pushed / no PR opened**.
- **Suite:** 247 tests green (`python3 -m pytest tests/ -q`).
- **⏭ NEXT: P4 — Skills** → [ADR-0005](0005-skills.md). Start with F12.1
  (`SKILL.md` format + `discover_skills` loader in `nerva_agent/skills.py`).
- **Deferred, don't forget:** `mcp add` subcommand + MCP HTTP/SSE transport
  (P3 shipped stdio + list/test only); F9 git-native undo → P8.

> How to resume: read this banner → open the NEXT phase's ADR → check its
> "Progress log" (bottom) for any partial work → implement to its test plan.

---

## How this directory works

- Every significant decision gets an **ADR** (Architecture Decision Record): a
  numbered, immutable-ish markdown file in `docs/adr/NNNN-title.md`.
- Each **development phase** has a dedicated ADR describing *what*, *why*, *how*,
  the exact files it touches, the seams it uses, the test plan, and its
  acceptance criteria — detailed enough to implement cold.
- **When a phase is worked on, its ADR and this README are updated** with real
  implementation notes, deviations, and a "Progress log" entry. Documentation is
  part of the definition of done for every phase.

### ADR status values
`Proposed` → `Accepted` → `In progress` → `Implemented` → (`Superseded by NNNN`)

---

## ADR index

| ADR | Title | Phase | Status |
|-----|-------|-------|--------|
| [0001](0001-offline-local-llm-interface.md) | Offline local LLM interface | — | Implemented |
| [0002](0002-monorepo-package-split.md) | Monorepo package split | — | Implemented |
| [0003](0003-local-agent-harness.md) | Local agent harness (one loop, two front-ends) | — | Implemented |
| [0010](0010-undo-hardening.md) | Undo checkpointing & test debt (bridge) | P2.5 | Implemented |
| [0004](0004-mcp-integration.md) | MCP — external tools over the wire | P3 | Implemented |
| [0005](0005-skills.md) | Skills — reusable packaged workflows | P4 | Proposed |
| [0006](0006-loops.md) | Loops — autonomous & recurring runs | P5 | Proposed |
| [0007](0007-resume-session-persistence.md) | Resume & session persistence | P6 | Proposed |
| [0008](0008-code-graph.md) | Code graph — repo-scale reasoning | P7 | Proposed |
| [0009](0009-subagents-and-git-undo.md) | Sub-agents & git-native undo | P8 | Proposed |

---

## Phase roadmap at a glance

The order follows the **dependency chain and risk**, not ambition. MCP and
Skills extend the existing tool registry (low risk, seams pre-cut). Loops need
undo to be bulletproof first. The graph is the deepest change and the biggest
capability jump.

| Phase | Pillar | Depends on | Risk | Status |
|-------|--------|-----------|------|--------|
| **P0** | Engine + CLI (F1–F7, routing, capacity) | — | — | ✅ Shipped |
| **P2.5** | Undo hardening (F8/F9) + tests | — | Low | ✅ Shipped (F9 git-undo → P8) |
| **P3** | **MCP** integration | P2.5 seams | Low | ✅ Shipped (add/HTTP deferred) |
| **P4** | **Skills** | project_context patterns | Low–Med | ⬜ **Next up** |
| **P5** | **Loops** (autonomous) | P2.5 hardened, P6 journal | Med | ⬜ After safety floor |
| **P6** | Resume / persistence | aibot_storage hooks | Low | ⬜ Small, unblocks P5 journal |
| **P7** | **Code graph** | agent_ignore | High | ⬜ Horizon (deep bet) |
| **P8** | Sub-agents + git-undo | agent_router, P4 | High | ⬜ Horizon |

Visual roadmap artifact: see the shared Revenant roadmap page.

---

## Pre-cut seams (why the plan is credible)

The engine was written with these extension points already in place. Each phase
plugs into one — it is not greenfield:

| Seam | Where | Feeds phase |
|------|-------|-------------|
| `[[mcp.servers]]` reserved config section | `revenant_cli/config.py` (docstring "reserved for F11") | P3 |
| `mcp` / `resume` subcommand stubs | `revenant_cli/cli.py` `_SUBCOMMANDS` | P3, P6 |
| `ToolRegistry` (pluggable, name→Tool) | `nerva_agent/agent_tools.py` | P3, P4, P7, P8 |
| `before_tool` lifecycle hook | `nerva_agent/agent_loop.py` | P2.5, P5 |
| `AgentResult.messages` transcript threading | `agent_loop.py`, used by REPL | P5, P6 |
| Role router "reusable by future agent loop" | `nerva_agent/agent_router.py` | P8 sub-agents |
| `ConversationStore` "Phase 3" summary hooks | `nerva_core/aibot_storage.py` | P6 |
| Ignore-glob engine | `nerva_agent/agent_ignore.py` | P7 |
| Checkpoint docstring names git as the shell-undo layer | `revenant_cli/checkpoint.py` | P8 |

---

## Cross-cutting invariants (apply to every phase)

1. **Offline always** (ADR-0001). No cloud SDKs, no telemetry, no keys. New
   network calls target the local Ollama server or a user-configured local MCP
   server only.
2. **Acyclic deps** (ADR-0002). Code lands in the lowest package that can hold
   it: `nerva-core` (LLM/storage) ← `nerva-agent` (loop/tools) ← `revenant-cli`.
3. **Per-slice tests + PR-per-feature.** Every F-slice ships with tests and its
   own PR. Current suite: **194 tests**.
4. **Degrade gracefully.** A failing optional dependency, MCP server, or model
   probe must never crash the CLI — it falls back.
5. **Approval gate is sacred.** Any new mutating capability routes through the
   existing `requires_approval` / `before_tool` machinery. Undo must cover it.
6. **Docs are done-criteria.** Ship a phase → update its ADR + this README.

---

## Immediate debt

- ~~Undo (F8/F9) lacks tests.~~ **Cleared 2026-07-30** — 23 tests, suite 217.
- ~~P3 MCP.~~ **Shipped 2026-07-30** — 29 tests, suite 247. `mcp add` action and
  the HTTP/SSE transport were deferred (stdio only); note both when they're needed.
- Next actionable phase: **P4 Skills** ([ADR-0005](0005-skills.md)).

---

## Progress log

> One entry per working session touching the roadmap. Newest first.

### 2026-07-30 (c) — P3 MCP shipped
- `nerva_agent/mcp_client.py` (stdlib JSON-RPC over stdio) + `mcp_tools.py`
  (remote tool → registry `Tool`, approval-by-default, read_only relax).
- `config.py` `mcp_server_specs` reads the reserved `[[mcp.servers]]` section;
  `_build_agent` loads MCP tools in write mode and closes clients on exit.
- `revenant mcp list|test` subcommand (replaces the stub). `mcp add` + HTTP
  transport deferred.
- **29 new tests → suite 247.** Verified end-to-end through the real CLI against
  a fake stdio server; fixed an argparse flag-ordering bug the unit tests missed.
- ADR-0004 → Implemented. **Next: P4 Skills (ADR-0005).**

### 2026-07-30 (b) — Undo test debt cleared → P2.5 shipped
- Added `tests/test_checkpoint.py` (16 tests) and 7 undo cases in
  `tests/test_cli.py`: snapshot/restore, new-file delete, run_bash skip,
  undo ordering, persist↔load round-trip, and every degradation path.
- Suite **194 → 217 green**. ADR-0010 → Implemented; P5's safety floor is set.
- **Next up: P3 MCP (ADR-0004).**

### 2026-07-30 (a) — Roadmap & ADRs established
- Completed the in-tree F8/F9 undo wiring: added `_checkpoint_store()` and
  `cmd_undo` in `cli.py`; verified end-to-end across processes; full suite green
  (194 tests). Undo still lacks unit tests → ADR-0010.
- Created this ADR directory: backfilled ADR-0001..0003, wrote ADR-0004..0010
  covering all remaining phases.

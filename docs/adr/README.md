# Revenant — Architecture Decisions & Development Roadmap

> **This is the resume-from-cold document.** If a working session is lost, start
> here. It records every architectural decision (ADRs) and the full phase-wise
> plan in enough detail to pick up implementation without re-deriving context.

**Last updated:** 2026-07-30 · **Tests:** 290 green
**Repo:** local, offline coding-agent CLI over Ollama · packages: `nerva-core ← nerva-agent ← revenant-cli`

---

## ▶ START HERE — current state (resume point)

- **Done:** P0 (engine), **P2.5** undo+ADRs, **P3** MCP, **P4** Skills, **P6** Resume.
- **Branches on origin:** `phase2.5-undo-and-adr-roadmap` (`7fc5fa9`),
  `phase3-mcp-integration` (`6226776`+`6a3643b`), `phase4-skills` (`49100c7`).
  **P6 is committed on the next branch (see git log); no PRs opened yet.**
- **Suite:** 290 tests green (`python3 -m pytest tests/ -q`).
- **⏭ NEXT: P5 — Loops** → [ADR-0006](0006-loops.md). Its safety floor (undo,
  P2.5) and run-journal dependency (Resume, P6) are both now in place — no more
  blockers. Start with F13.1 (iterate-until-done driver).
- **Deferred, don't forget:** one-shot `run` autosave/resume (P6);
  `revenant run --skill` (P4); `mcp add` + MCP HTTP/SSE transport (P3);
  F9 git-native undo → P8.

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
| [0005](0005-skills.md) | Skills — reusable packaged workflows | P4 | Implemented |
| [0006](0006-loops.md) | Loops — autonomous & recurring runs | P5 | Proposed |
| [0007](0007-resume-session-persistence.md) | Resume & session persistence | P6 | Implemented |
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
| **P4** | **Skills** | project_context patterns | Low–Med | ✅ Shipped (run --skill deferred) |
| **P5** | **Loops** (autonomous) | P2.5 hardened, P6 journal | Med | ⬜ **Next up** (blockers cleared) |
| **P6** | Resume / persistence | aibot_storage hooks | Low | ✅ Shipped (per-ws JSON) |
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
- ~~P4 Skills.~~ **Shipped 2026-07-30** — 25 tests, suite 272. `run --skill`
  one-shot deferred (needs the `run` goal positional to become optional).
- ~~P6 Resume.~~ **Shipped 2026-07-30** — 18 tests, suite 290. Per-workspace JSON
  sessions; `chat` auto-saves; `resume list`/`resume <id>`. One-shot `run`
  autosave deferred. This unblocks the P5 run journal.
- Next actionable phase: **P5 Loops** ([ADR-0006](0006-loops.md)) — no blockers left.

---

## Progress log

> One entry per working session touching the roadmap. Newest first.

### 2026-07-30 (e) — P6 Resume shipped
- `revenant_cli/session_store.py`: per-workspace JSON sessions under
  `<ws>/.aibot/sessions/` (chose this over the shared ConversationStore DB so
  sessions travel with the repo). save/load/list/latest, all best-effort.
- `cmd_chat` auto-saves after every turn; `cmd_resume` handles `list` / `<id>` /
  latest and re-hydrates the transcript back into the REPL.
- **18 new tests → suite 290.** Verified end-to-end (chat→save→resume threads
  history back). One-shot `run` autosave deferred.
- ADR-0007 → Implemented. **P5 Loops now has no blockers — next.**

### 2026-07-30 (d) — P4 Skills shipped
- `nerva_agent/skills.py`: `Skill` + `discover_skills` (`+++` TOML frontmatter
  via stdlib tomllib), `render_skill_index` (progressive disclosure),
  `compose_skill_body` (body injection), `scope_registry` (tool scoping that
  composes with the P3 MCP tools).
- `cli.py`: `_skill_dirs`/`_load_skills`; skill index folded into the preamble;
  `revenant skills list|show`; REPL `/skills` and `/skill <name>` (loads body,
  scopes tools, runs the body as the turn goal).
- Frontmatter format decided as **`+++` TOML** (user call) — zero new deps.
- **25 new tests → suite 272.** `skills list/show` verified end-to-end.
- `run --skill` one-shot deferred. ADR-0005 → Implemented.
- **Next: P6 Resume (ADR-0007), then P5 Loops (ADR-0006).**

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

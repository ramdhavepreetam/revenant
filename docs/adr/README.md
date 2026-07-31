# Revenant — Architecture Decisions & Development Roadmap

> **This is the resume-from-cold document.** If a working session is lost, start
> here. It records every architectural decision (ADRs) and the full phase-wise
> plan in enough detail to pick up implementation without re-deriving context.

**Last updated:** 2026-07-31 · **Tests:** 393 green · **Shipped: v0.2.0 on PyPI** · **Building: H-series (0.3.0)**
**Repo:** local, offline coding-agent CLI over Ollama · packages: `nerva-core ← nerva-agent ← revenant-cli`

---

## ▶ START HERE — current state (resume point)

- **🎉 P0–P8 all Implemented and merged to `master`.** Every next-level pillar
  (MCP, Skills, Loops, Graph) plus Resume, hardened undo, sub-agents, and
  git-native undo. All 14 PRs merged.
- **📦 Shipped to PyPI: v0.2.0** (`pip install -U revenant-cli`) — tag `v0.2.0`.
- **Suite:** 366 tests green (`python3 -m pytest tests/ -q`).
- **🔨 Building the H-series (0.3.0): make the small local model punch above its
  weight.** Strategy [ADR-0011](0011-harness-carries-the-model.md).
  **H1 verify→repair is SHIPPED** [ADR-0012](0012-verify-repair-loop.md) — edits
  are now checked and repaired before the user sees them. **⏭ NEXT: H0 eval**
  [ADR-0015](0015-eval-harness.md) (measure H1's lift) or **H2** context injection
  [ADR-0013](0013-proactive-context-injection.md).
- **Deferred backlog** (optional polish, nothing blocked): loop `--every` (P5);
  one-shot `run` autosave (P6); `mcp add` + MCP HTTP (P3); role-routed sub-agents
  (P8); persisting the code graph (P7).
- **Done since the roadmap:** user docs refreshed (#11); `run --skill` + loop
  `--watch` (#12); graph F14.3/F14.4 (#13); **0.2.0 released to PyPI** (#14).

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
| [0006](0006-loops.md) | Loops — autonomous & recurring runs | P5 | Implemented (triggers deferred) |
| [0007](0007-resume-session-persistence.md) | Resume & session persistence | P6 | Implemented |
| [0008](0008-code-graph.md) | Code graph — repo-scale reasoning | P7 | Implemented |
| [0009](0009-subagents-and-git-undo.md) | Sub-agents & git-native undo | P8 | Implemented |
| [0011](0011-harness-carries-the-model.md) | **The harness carries the model** (H-series strategy) | 0.3.0 | Accepted |
| [0012](0012-verify-repair-loop.md) | Verify → repair loop | H1 | Implemented (targeted-tests deferred) |
| [0013](0013-proactive-context-injection.md) | Proactive context injection | H2 | Proposed |
| [0014](0014-decompose-and-per-step-verify.md) | Decompose + per-step verify | H3 | Proposed |
| [0015](0015-eval-harness.md) | Eval harness (measure the lift) | H0 | Implemented |

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
| **P5** | **Loops** (autonomous) | P2.5 hardened, P6 journal | Med | ✅ Shipped (triggers deferred) |
| **P6** | Resume / persistence | aibot_storage hooks | Low | ✅ Shipped (per-ws JSON) |
| **P7** | **Code graph** | agent_ignore | High | ✅ Shipped (ast; packing + re-index done) |
| **P8** | Sub-agents + git-undo | agent_router, P4 | High | ✅ Shipped (closes F9) |

Visual roadmap artifact: see the shared Revenant roadmap page.

### 0.3.0 — the H-series: harness carries the model

P0–P8 built the feature surface; the **H-series** makes a *small local model*
(targeted at a 14B) perform above its weight, by moving correctness out of the
model and into deterministic machinery. Strategy: [ADR-0011](0011-harness-carries-the-model.md).

| Phase | Pillar | Fixes | Reuses | Status |
|-------|--------|-------|--------|--------|
| **H1** | **Verify → repair loop** | plausible-but-broken edits | `before_tool` seam, loop-driver, undo, code-graph | ✅ Shipped (targeted-tests deferred) |
| **H2** | Proactive context injection | edits in the dark | `pack_symbol_context` (F14.3) | ⬜ Next |
| **H3** | Decompose + per-step verify | can't hold a long plan | sub-agents (P8), H1 | ⬜ Next |
| **H0** | Eval harness | — (measures the lift) | new; small | ✅ Shipped (5 tasks + `--compare`) |

**Governing rule:** the model proposes; the harness verifies and repairs. A model
mistake that reaches the user is a *harness* failure. Stand up **H0 early** so
**H1**'s impact is a number, not a vibe.

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
  autosave deferred.
- ~~P5 Loops.~~ **Shipped 2026-07-30** — 19 tests, suite 309. `revenant loop`
  with predicates + budgets + dry-run + run journal. **Triggers (F13.3
  `--every`/`--watch`) deferred.**
- ~~P7 Code graph.~~ **Shipped 2026-07-30** — 23 tests, suite 332. stdlib-`ast`
  indexer + `defn_of`/`who_calls`/`neighbors`/`impact_of` tools. **F14.3 packing
  + F14.4 incremental re-index deferred.**
- ~~P8 Sub-agents + git-undo.~~ **Shipped 2026-07-30** — 20 tests, suite 352.
  `spawn_subagent` tool + git-native whole-tree undo (**closes F9**). Role-routed
  sub-agents deferred.
- **No phase remains.** Remaining items are the optional deferred backlog above.

---

## Progress log

> One entry per working session touching the roadmap. Newest first.

### 2026-07-31 (b) — H1 verify→repair shipped
- `nerva_agent/verify.py`: verifier abstraction (PyCompile + Command + Composite).
- `agent_loop.py`: `after_tool` hook (mirrors `before_tool`) appends verify
  feedback to the observation the model repairs from.
- `revenant_cli/verify_hook.py` + `config.verify_config`: `[verify]` config,
  hook wiring in write mode, per-target repair budget with undo revert.
- **27 tests → suite 393.** Verified end-to-end: a fake model wrote broken Python,
  got the exact SyntaxError back, and repaired it — broken code never shipped.
- H1.4 (graph-targeted test selection) deferred. **The core of ADR-0011 is live.**

### 2026-07-31 (a) — v0.2.0 released; H-series (0.3.0) planned
- **Released 0.2.0 to PyPI** (nerva-core / nerva-agent / revenant-cli), tag
  `v0.2.0`; verified via a real-PyPI clean-venv install.
- **Planned the H-series** — the 0.3.0 theme: *the harness carries the model*.
  Strategy ADR-0011 + phase ADRs 0012 (H1 verify→repair, lead), 0013 (H2 context
  injection), 0014 (H3 decompose), 0015 (H0 eval). Grounded in a real assessment:
  the loop has no post-edit verification and the code graph is pull-only.
- Next: stand up H0 eval + build H1 verify→repair.

### 2026-07-30 (h) — P8 Sub-agents + git-native undo shipped → ROADMAP COMPLETE
- `nerva_agent/subagent.py`: `spawn_subagent` tool — delegates a scoped sub-goal
  to a nested `AgentLoop` (injected `loop_factory`), returns a bounded summary;
  depth cap prevents runaway recursion; mutating ⇒ approval-gated.
- `revenant_cli/git_checkpoint.py`: `GitCheckpointer` — whole-tree undo via
  `git stash create` shadow-commits under `refs/revenant/undo/*`; reverts tracked
  edits + removes `run_bash` artifacts (`checkout` + `clean -fd`). `_build_agent`
  and `cmd_undo` use it when the workspace is a git repo, else file-snapshots.
  **Closes F9.**
- **20 new tests → suite 352.** Verified end-to-end via the real CLI: an edit AND
  a run_bash-created file both reverted; user branches untouched.
- ADR-0009 → Implemented. **P0–P8 are all Implemented — the roadmap is done.**

### 2026-07-30 (g) — P7 Code graph shipped
- `nerva_agent/code_graph/indexer.py`: stdlib-`ast` indexer (Python exact, regex
  fallback for other langs) → `CodeGraph` of file/symbol nodes + defines/imports/
  calls edges; ignore-aware; never raises. Chosen over tree-sitter (zero deps).
- `code_graph/tools.py`: `defn_of`, `who_calls`, `neighbors`, `impact_of` as
  read-only registry tools. Wired into `_build_agent` in every mode; `--no-graph`
  opts out; indexing failure degrades to a warning.
- **23 new tests → suite 332.** Verified on THIS repo: `who_calls('dispatch')` →
  `AgentLoop.run`; 148 symbols / 18 files / 0 parse errors.
- **F14.3 (structure-aware packing) + F14.4 (incremental re-index) deferred.**
  ADR-0008 → Implemented. **Next: P8 (ADR-0009) — the last phase.**

### 2026-07-30 (f) — P5 Loops shipped
- `nerva_agent/loop_driver.py`: `loop_until` (inject `run_fn`, thread history,
  nudge on not-yet), `Budget`, and built-in predicates (model-final, command
  exit-0, file-exists). Autonomy always bounded — no run-forever mode.
- `revenant loop` subcommand: `--until`/`--until-tests`/`--until-file`,
  `--max-iterations`/`--max-wall`, `--autonomous` (yolo within budget + a
  per-iteration checkpoint boundary for undo), `--dry-run` (read-only preview).
  Each iteration is journaled as a resumable session (F13.4, reuses P6).
- **19 new tests → suite 309.** Verified end-to-end (fake agent satisfies a file
  predicate on iteration 2 → stops, exit 0).
- **F13.3 triggers (`--every`/`--watch`) deferred.** ADR-0006 → Implemented.
- **Next: P7 Code graph (ADR-0008) or P8 (ADR-0009).**

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

# ADR-0009 — Sub-agents & git-native undo (Phase 8)

- **Status:** Implemented
- **Phase:** P8 (horizon, composition layer) · **F-slices:** F15.1 sub-agent spawn, F16.1 git checkpointing (closes F9)
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
- **Depends on:** ADR-0003 (router, loop), ADR-0005 (skill scoping), ADR-0010 (undo) · **Blocks:** —

## Context
Two composition upgrades that both build on existing seams:
1. **Sub-agents** — delegate a sub-goal to a fresh, budgeted `AgentLoop` with its
   own scoped registry, returning a summary. The router was written to be
   "reusable by the future coding agent loop" — sub-agents are the loop calling
   itself.
2. **Git-native undo** — `checkpoint.py`'s own docstring says *"git integration
   (F9) is the right undo layer for shell side effects."* File-snapshots can't
   capture what `run_bash` does; shadow-commits can.

## Decision
Add a **sub-agent spawn tool** and a **git shadow-commit checkpointer** that
extends undo to shell side-effects. Both are opt-in and bounded.

### F15.1 — Sub-agent spawn tool (`nerva_agent/subagent.py`)
- A `Tool` `spawn_subagent(goal, tools?, role?, budget?)`:
  - Builds a nested `AgentLoop` with its own `ToolRegistry` (a scoped subset,
    reusing the skill tool-filter from ADR-0005), its own step/token budget, and
    a role resolved via `agent_router`.
  - Runs to completion; returns a **summary** (not the full transcript) as the
    observation — keeping the parent's context small.
  - `mutating=True` (a sub-agent can edit) ⇒ `requires_approval`; the parent's
    checkpointer wraps the whole sub-run as one undo boundary.
- Guardrails: max spawn depth (no infinite recursion), inherited budgets,
  offline invariant preserved.

### F16.1 — Git-native checkpointing (`revenant_cli/git_checkpoint.py`)
- If the workspace is a git repo, before a mutating boundary create a
  **shadow-commit** (or `git stash create`-style object) that captures the full
  tree state — including files touched by `run_bash`.
- `revenant undo` gains the ability to restore to a shadow-commit, superseding
  file-snapshots for shell effects (file-snapshots remain the fallback for
  non-git workspaces).
- Shadow-commits live on a ref namespace (e.g. `refs/revenant/undo/*`) so they
  never pollute the user's branches or history.

## Failure & degradation
- Non-git workspace → fall back to file-snapshot undo (ADR-0010).
- Sub-agent budget exhausted → return a partial summary + status; parent decides.
- Spawn depth exceeded → refuse with a clear observation.

## Test plan — DONE (20 tests, 2026-07-30)
`tests/test_subagent.py` (9, fake nested loop):
- [x] spawn runs a nested loop; returns a bounded summary (not the transcript).
- [x] scoped tool list + incremented depth passed to the factory.
- [x] depth cap refuses recursion (factory not called); empty goal refused.
- [x] tool is mutating ⇒ approval-gated; factory/run errors → observation.
`tests/test_git_checkpoint.py` (7, temp git repo):
- [x] shadow-commit captures a `run_bash`-created untracked file; undo removes it.
- [x] undo restores a tracked edit; refs deleted after undo; `clear`.
- [x] non-git workspace detected (toplevel==workspace) → caller falls back.
- [x] shadow refs live under `refs/revenant/undo/*`, not user branches.
`tests/test_cli.py` (4):
- [x] `spawn_subagent` registered in write mode, absent read-only.
- [x] `cmd_undo` uses git-native path in a repo (reverts edit + shell artifact);
      empty-git case.

## Acceptance criteria
- [x] A parent agent delegates a sub-goal and gets a concise summary back.
- [x] `revenant undo` reverts shell side-effects in a git workspace (verified
      end-to-end: edit + a run_bash-created file both reverted via the real CLI).
- [x] No pollution of user branches (refs under `refs/revenant/undo/*`, hidden
      from `git branch`); offline; tests green (332 → 352); ADRs + README updated.

## Implementation notes (what actually shipped)
- **F15.1** `nerva_agent/subagent.py`: `build_spawn_tool(loop_factory, depth,
  max_depth)`. The factory is injected (CLI's `_make_subagent_factory` rebuilds a
  full agent via `_build_agent` with a deeper `_subagent_depth` and, if named, a
  `scope_registry`-scoped tool set). Returns a bounded summary from the sub-run's
  `AgentResult`. Depth cap (default 2) prevents runaway recursion.
- **F16.1** `revenant_cli/git_checkpoint.py`: `GitCheckpointer` uses `git stash
  create` to capture the whole tree as a shadow-commit under
  `refs/revenant/undo/*`; `_restore` does `checkout <sha> -- .` (tracked) + `git
  clean -fd` (drop untracked run_bash artifacts; ignored files preserved).
  `_build_agent` and `cmd_undo` pick git-native when `is_git_repo(workspace)`
  (toplevel must equal the workspace), else the file-snapshot store. **This
  closes F9.**
- **Deviation:** sub-agents don't yet route to a *different* role model via
  `agent_router` (they inherit the parent config); role-based sub-agents are a
  small follow-up. Undo `clear()` on accepted runs is available but not
  auto-invoked.

## Progress log
- 2026-07-30 — Proposed. Both halves foreshadowed in existing docstrings.
- 2026-07-30 — **Implemented.** Sub-agent spawn tool + git-native whole-tree undo
  (closes F9). 20 tests, suite 332 → 352. Verified end-to-end via the real CLI.
  **This is the final roadmap phase — P0..P8 all Implemented.**

# ADR-0009 — Sub-agents & git-native undo (Phase 8)

- **Status:** Proposed
- **Phase:** P8 (horizon, composition layer) · **F-slices:** F15.1 sub-agent spawn, F16.1 git checkpointing
- **Date proposed:** 2026-07-30 · **Date implemented:** —
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

## Test plan
`tests/test_subagent.py` (fake nested loop):
- [ ] spawn runs a nested loop with a scoped registry + budget; returns a summary.
- [ ] depth limit refuses recursion beyond the cap.
- [ ] parent checkpoint wraps the sub-run as one undo boundary.
`tests/test_git_checkpoint.py` (temp git repo):
- [ ] shadow-commit captures a `run_bash`-created file; undo restores tree.
- [ ] non-git workspace falls back to file-snapshots.
- [ ] shadow refs live under `refs/revenant/undo/*`, not user branches.

## Acceptance criteria
- [ ] A parent agent delegates a sub-goal and gets a concise summary back.
- [ ] `revenant undo` reverts shell side-effects in a git workspace.
- [ ] No pollution of user branches; offline; tests green; ADRs + README updated.

## Progress log
- 2026-07-30 — Proposed. Both halves foreshadowed in existing docstrings.

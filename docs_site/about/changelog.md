# Changelog

All notable changes to Revenant are documented here. This project follows
[Semantic Versioning](https://semver.org/).

---

## [0.3.0] — Harness release

The **H-series**: making a small local model (targeted at a 14B) perform above
its weight by moving correctness out of the model and into deterministic
machinery — "the model proposes; the harness verifies and repairs." Fully
offline, no new runtime dependencies. Backward-compatible with 0.2.0: existing
`run`/`chat` usage is unchanged; every new feature is opt-in.

### Added

- **Verify → repair** — with `[verify]` enabled, every edit is checked
  (byte-compile + your configured commands, e.g. `pytest -q`); a failure is fed
  back to the model to fix before the run finishes, bounded by
  `max_repair_attempts` and reverted via undo on exhaustion. Broken code is
  caught, not shipped.
- **Proactive context** — with `[context]` enabled, a symbol's definition and
  callers are surfaced automatically when it's edited, and symbols in an
  error/traceback are resolved to their definitions — the harness pushes the code
  context instead of waiting for the model to ask.
- **`revenant run --plan`** — decompose a larger goal into small steps and run
  them one at a time, each verified before the next, so the model isn't asked to
  hold the whole task in its head.
- **Eval harness** (`evals/`) — a task suite + runner to measure harness lift on
  a fixed model, with `--compare` (on-vs-off) and `--repeat N` (average out model
  non-determinism, reporting per-task and attempt-level pass-rates).

### Fixed

- **Verifier no longer false-fails on a path-scoped check with no changed files.**
  A `[verify]` command containing `{paths}`/`{tests}` (e.g. `py_compile {paths}`)
  ran with an empty substitution after a shell step and reported a bogus failure;
  it is now skipped when there are no changed paths (a checker invoked wrong
  degrades, it does not fail the edit).

---

## [0.2.0] — Extensibility release

Adds the extensibility layer — external tools, reusable workflows, autonomous
runs, and repo-scale reasoning — all fully offline with no new runtime deps.
Backward-compatible with 0.1.0: existing `run`/`chat` usage is unchanged.

### Added

- **`revenant loop`** — run a goal autonomously until a condition is met
  (`--until`, `--until-tests`, `--until-file`), bounded by `--max-iterations` /
  `--max-wall`, with `--autonomous` (per-iteration undo checkpoint) and
  `--dry-run` (zero-write preview).
- **`revenant undo`** — revert a run's changes. Git-native whole-tree undo in git
  workspaces (reverts `run_bash` side-effects too, via private
  `refs/revenant/undo/*` refs); file-snapshot undo elsewhere.
- **MCP** — call tools from Model Context Protocol servers configured in
  `[[mcp.servers]]`; inspect with `revenant mcp list|test`.
- **Skills** — reusable `SKILL.md` workflows with progressive disclosure and
  optional tool scoping; `revenant skills list|show` and `/skill` in `chat`.
- **`revenant resume`** — save and resume sessions; `chat` and `loop` auto-save.
- **Code graph** — a symbol/dependency index with `defn_of`, `who_calls`,
  `neighbors`, and `impact_of` tools (stdlib `ast`; `--no-graph` to skip).
  Supports structure-aware context and incremental single-file re-indexing.
- **Sub-agents** — the `spawn_subagent` tool delegates a scoped sub-goal to a
  nested, budgeted agent.
- **Project config** — layered `.revenant.toml` (flag › project › user › default).
- **One-shot skills** — `revenant run --skill <name>` runs a skill's procedure.
- **Loop watch** — `revenant loop --watch '<glob>'` re-runs on file changes.

### Notes

- No new required runtime dependencies; Revenant stays fully offline.
- Requires an [Ollama](https://ollama.com) server with a tool-capable model
  (e.g. `ollama pull qwen2.5-coder:7b`).

---

## [0.1.0] — Initial release

The first public release of Revenant.

### Added

- **`revenant` CLI** — a local, offline coding agent that runs a tool-using loop
  against your own Ollama models.
- **Agent loop** with read-only tools (`read`, `list`, `glob`, `grep`) and
  approval-gated mutating tools (`write`, `edit`, `bash`).
- **Dual tool-call protocol** — native `tool_calls` plus a prompt-based `action`
  fallback, auto-detected and cached per model.
- **Model routing** by role (`code`, `language`, `router`, `fallback`) via a
  heuristic + tiny classifier.
- **Context compaction** — folds old steps into a recap when over the context
  budget.
- **Hardware-aware tuning** — `max_steps` and context budget derived from RAM.
- **Safety** — path-confined workspace and a destructive-command block list
  active even in `--yolo`.
- **Distribution** — published to PyPI (`nerva-core`, `nerva-agent`,
  `revenant-cli`); macOS `.dmg` and Windows `.exe` installers via CI on `v*`
  tags.

!!! note "Immutable releases"
    PyPI versions are permanent. Fixes ship as a new version.

---

[0.3.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.3.0
[0.2.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.2.0
[0.1.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.1.0

# Changelog

All notable changes to Revenant are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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

[0.2.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.2.0
[0.1.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.1.0

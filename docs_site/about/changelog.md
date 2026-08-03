# Changelog

All notable changes to Revenant are documented here. This project follows
[Semantic Versioning](https://semver.org/).

---

## [0.8.0] — 2026-08-03: memory

The coding agent gains **persistent, project-level memory** across runs — offline,
with **zero new dependencies** (stdlib SQLite + FTS5). Backward-compatible;
everything is gated and additive.

### Added

- **Cross-session memory** — the coding agent now remembers durable facts about a
  project across runs (conventions, where things live, pitfalls to avoid), stored
  under `.aibot/memory.db`. Fully offline, **zero new dependencies** (stdlib SQLite
  + FTS5). Relevant memories are recalled into the agent's context at the start of
  a later run.
  - The agent has `remember` / `recall` tools it uses mid-task.
  - After a run, it may **propose** a few durable facts — each is **confirmed
    before it's saved** (never auto-written; nothing persists without a yes).
  - `revenant memory list / show <id> / forget <id> / clear` to inspect and prune
    it; `/memory` in the TUI. `--no-memory` / `--no-memory-suggest` to disable.

---

## [0.7.0] — 2026-08-02: usability

A friction-reducing pass driven by real first-run feedback: the CLI starts where
you want, config is editable without touching TOML, and model/mode are switchable
without restarting. Backward-compatible with 0.6.x.

### Added

- **Bare `revenant` opens chat** — running `revenant` with no arguments now starts
  interactive chat (the TUI when available, else the REPL). `revenant "<goal>"`
  still runs one-shot.
- **Shift+Tab toggles the approval mode in the TUI** — cycle between
  `approval-gated` (asks before edits) and `yolo` (auto-approve) live, without
  restarting. The current mode is shown on a dedicated line **below the input**.
  (Read-only stays a launch flag — it's enforced by removing edit tools at start.)
- **`revenant config show` / `config set`** — inspect the resolved configuration
  (with each value's source) and set values without hand-editing TOML:
  `revenant config set model=qwen2.5:7b` (`--project` for the repo config). No more
  hitting the model picker every run.
- **In-session model switch** — `/model <name>` in the TUI switches the running
  model live (no restart); `/model` with no argument shows the current one.
- **`/mode` command** — a discoverable palette entry that cycles the approval mode
  (the same action as Shift+Tab).
- **Friendlier first-run setup** — when your configured model isn't pulled, the
  picker message is clearer and remembers your choice; a non-interactive run
  (piped/CI) auto-selects a sensible pulled model (preferring a coder model)
  instead of failing.

---

## [0.6.1] — 2026-08-01: patch

### Added

- **`revenant --version`** — prints the installed version (read from package
  metadata) and exits. Also shown in `--help`.

---

## [0.6.0] — 2026-08-01: faster, deeper, measurable (W-series)

The **W-series**: the agent gets **faster to watch**, **deeper in what it can
safely do**, and **measurably better**. Backward-compatible with 0.5.0 — streaming
and the new tools degrade or default off where appropriate.

### Added

- **Token-by-token streaming** — the assistant's reply now streams live as it
  generates, both in `revenant chat` (inline) and the TUI (a live in-place line).
  On by default in an interactive terminal; `--stream`/`--no-stream` to control it.
  Works on both the prompt-based and native tool-calling paths (the tool call
  arrives whole; only the content prefix streams).
- **Atomic project-wide edits** — a new `apply_edits` tool applies a set of edits
  across many files **all-or-nothing**: if any edit fails, every change is rolled
  back, so a rename is never left half-applied. `edit_file` gains `all=true` to
  replace every occurrence in a file (default stays exactly-one).
- **`revenant loop --every <seconds>`** — re-run a goal on a fixed interval (a
  time-triggered companion to `--watch`), within the existing budget.
- **Persisted, incremental code graph** — the code graph is cached under
  `.aibot/code_graph.json` and only re-indexes files that changed since last run
  (a corrupt cache falls back to a full rebuild). `--no-graph-cache` opts out.
- **Role-routed sub-agents** — `spawn_subagent(role=…)` runs a sub-agent under a
  different model (via the role router), e.g. a stronger planner delegating cheap
  mechanical work. No role = the parent's model, as before.
- **`revenant mcp add <name>`** — add an MCP server to your config from the CLI
  (stdio or the new HTTP/SSE transport), instead of hand-editing `[[mcp.servers]]`.
- **MCP over HTTP/SSE** — MCP servers can now be reached over HTTP/SSE (a local
  URL), not just stdio subprocesses. Fully offline: the URL is a local server.

### Changed

- **Measurable harness** — the eval harness (`evals/run.py`) now records
  step-count, token-cost, and edit-precision per task (not just pass/fail);
  `--compare` diffs them, and `--gate baseline.json` fails CI on a regression.
  Three project-wide-rename tasks were added. New `AgentEvent` kind (`token`) and
  the `ToolParam` array-of-objects shape are additive — existing consumers and
  scalar tools are byte-identical.

---

## [0.5.0] — 2026-08-01: the interactive terminal (V-series)

A **Claude-Code-like full-screen terminal** for `revenant chat`. Backward
compatible: the TUI is opt-in via an optional dependency, and everything falls
back to the existing REPL when it's absent, piped, or disabled.

### Added

- **Interactive TUI** — `pip install "revenant-cli[tui]"` then `revenant chat`
  (auto-on in a terminal; force with `--tui`, disable with `--no-tui` or
  `NO_COLOR`). A persistent input box, a live streaming activity view, and a
  status bar showing **model · workspace · mode · a live context-size gauge**.
- **Discoverable slash commands** — type `/` to open a menu of every command
  **and** skill with a one-line description: `/help /skills /skill <name> /model
  /context /agents /reset /clear /exit`. No more memorizing.
- **Multi-agent visibility** — when the agent spawns sub-agents, they appear in
  **coloured, indented lanes** with a live sub-agent count, so you can watch the
  delegation happen.
- **Interrupt without quitting** — `ctrl-c` cancels the current goal cooperatively
  (the loop stops cleanly between steps); `ctrl-d` quits, `ctrl-l` clears the log.
- **In-app approvals** — mutating tools prompt in a modal with a **real edit
  diff**; approve with `y`, deny with `n`/`esc`.

### Changed

- `AgentLoop.run` gained an optional `should_stop` predicate (powers the TUI's
  interrupt); default behavior is unchanged. New event kinds (`context`,
  `agent_start`, `agent_end`, `interrupted`) and optional `AgentEvent` fields
  (`agent`, `context`) are additive — the plain and rich consoles are unaffected
  (plain output stays byte-identical).

---

## [0.4.0] — Usability release

The **U-series**: making the CLI genuinely usable — a live console that shows
what the agent is doing, and a friction-free first-run setup. Backward-compatible
with 0.3.0; the rich console is opt-in and everything else degrades gracefully.

### Added

- **Live rich console** — `pip install "revenant-cli[rich]"` adds a
  "thinking…" spinner while the model works, **real syntax-highlighted diffs** in
  the approval prompt, and a session header. `rich` is an **optional dependency**;
  without it the CLI produces the same plain-ANSI output as before. The standalone
  `.dmg` / `.exe` installers bundle it.
- **`revenant doctor`** — checks Ollama reachability, lists pulled models, shows
  the config a run will resolve, and tells you if you're ready. Exit `0`/`1`.
- **`revenant models`** — lists the models pulled on the Ollama server.
- **Pre-flight setup check** — before each run, if Ollama isn't running or the
  model isn't pulled, the CLI stops with the exact fix (`ollama serve` /
  `ollama pull …`) and offers a picker of your pulled models. `--skip-preflight`
  bypasses; `OLLAMA_HOST` and `NO_COLOR` are honored.

### Fixed

- **First run now works by the docs.** The default `code` model resolved to a 14b
  the quickstart never told you to pull; `code`/`router`/`summary` now default to
  `qwen2.5-coder:7b`, so one `ollama pull qwen2.5-coder:7b` is enough.
- **Actionable model errors** — a connection failure or missing-model error now
  appends the fix (`ollama serve` / `ollama pull`).
- **macOS / Windows installers attach to the GitHub Release** (the release job
  gained `contents: write`; had failed on 0.2.0/0.3.0).

### Changed

- **Release CI gates on the Windows installer** installing + running end-to-end
  before publishing.

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

[0.4.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.4.0
[0.3.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.3.0
[0.2.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.2.0
[0.1.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.1.0

# Revenant

**A local, offline coding-agent CLI powered by your own [Ollama](https://ollama.com) models.**
No cloud, no telemetry, no API keys.

Revenant is "Claude Code, but for your private LLM": it runs a tool-calling agent
loop entirely on your machine — reading and searching your code, and (with your
approval) editing files and running shell commands.

> The companion web app (**AIBot**) lives in a separate, private repository. This
> repo is the **public CLI** and its shared engine only.

## Layout (pip-installable packages)

```
packages/
  nerva-core/     shared: LLM layer + memory/profiles/storage
  nerva-agent/    the agent engine (tool loop, protocol, tools, routing, capacity)
  revenant-cli/   the `revenant` command   (depends on the two above)
docs_site/        Material for MkDocs documentation source
packaging/        PyInstaller spec, Inno Setup script, installers
tests/            pytest suite
```

Dependency graph (acyclic): `nerva-core ← nerva-agent ← revenant-cli`.

## Install

```bash
pip install revenant-cli          # the `revenant` command
pip install "revenant-cli[tui]"   # + the full-screen interactive terminal
```

Or grab a standalone macOS `.dmg` / Windows `.exe` from
[Releases](https://github.com/ramdhavepreetam/revenant/releases) (installers
bundle the TUI).

## Use

```bash
revenant                          # bare command opens interactive chat
revenant chat                     # same, explicitly
revenant "where is auth handled?" # one-shot run
revenant --read-only "summarize what packages/nerva-agent does"
revenant --workspace ~/proj "fix the failing test" --plan
```

With `[tui]` installed, `revenant chat` (or bare `revenant`) opens a full-screen
terminal: **token-by-token streaming**, a live activity view, a discoverable
slash-command palette (`/`), a context-size gauge, sub-agents in nested lanes,
in-app diff approvals, and a mode line you toggle with **Shift+Tab**
(approval-gated ↔ yolo). Without it (or `--no-tui`), it falls back to the plain
REPL. Needs [Ollama](https://ollama.com) with a model pulled.

## Features

- **Streaming TUI** — the assistant's reply streams live; interrupt a run with
  `ctrl-c` without quitting; switch model in-session (`/model`), cycle approval
  mode (`/mode` or Shift+Tab).
- **Editable config, no TOML wrangling** — `revenant config show` /
  `config set model=qwen2.5:7b`; friendly first-run model picker that remembers
  your choice.
- **Cross-session memory** — the agent remembers durable project facts across
  runs (offline, stdlib SQLite/FTS5, **zero extra deps**); inspect with
  `revenant memory list` / `/memory`.
- **Adaptive planning** (`--plan`) — a step that stumbles is retried, then the
  remaining steps re-planned, instead of aborting the run.
- **Phase-aware routing** — a stronger model drafts the plan while a cheaper one
  executes each step, when your machine has RAM for both.
- **Deep code tools** — a code graph (`defn_of` / `who_calls` / `impact_of`),
  atomic multi-file edits (`apply_edits`), verify→repair on edits, git-native undo.
- **Extensible & autonomous** — MCP servers (stdio or HTTP/SSE, `mcp add`),
  reusable skills, sub-agents, and bounded autonomous loops (`loop --until…`,
  `--every`, `--watch`).

Full docs: <https://ramdhavepreetam.github.io/revenant/>.

## Develop

```bash
make dev        # editable-install the 3 packages in dependency order
make test       # pytest
mkdocs build --strict   # build the docs
```

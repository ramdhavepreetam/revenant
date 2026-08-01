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
revenant --read-only "summarize what packages/nerva-agent does"
revenant --workspace ~/proj "where is auth handled?"
revenant chat                     # interactive TUI (type / for commands, ctrl-c to interrupt)
```

With `[tui]` installed, `revenant chat` opens a full-screen terminal: a live
activity view, a discoverable slash-command palette, a context-size gauge,
sub-agents shown in nested lanes, and in-app diff approvals. Without it (or with
`--no-tui`), it falls back to the plain REPL.

Needs [Ollama](https://ollama.com) running with a model pulled
(`ollama pull qwen2.5-coder:7b`). Full docs:
<https://ramdhavepreetam.github.io/revenant/>.

## Develop

```bash
make dev        # editable-install the 3 packages in dependency order
make test       # pytest
mkdocs build --strict   # build the docs
```

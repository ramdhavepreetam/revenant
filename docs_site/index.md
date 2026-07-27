# Revenant

**A local, offline coding-agent CLI powered by your own [Ollama](https://ollama.com) models.**

Revenant is "Claude Code, but for your private LLM." It runs a tool-calling agent
loop entirely on your machine: it reads and searches your code, and — with your
approval — edits files and runs shell commands. No cloud, no telemetry, no API keys.

!!! note "At a glance"
    | | |
    |---|---|
    | **Package** | `revenant-cli` (`pip install revenant-cli`) |
    | **Command** | `revenant` |
    | **Version** | 0.1.0 |
    | **Runtime** | Python 3.11+ · Ollama running locally |
    | **License / privacy** | Fully offline, on-prem — nothing leaves your machine |

---

## Why Revenant

- **Private by construction.** The model is your local Ollama model. No prompts,
  code, or telemetry ever leave the machine.
- **Real tool use.** Read, list, glob, and grep your codebase; write and edit
  files; run shell commands — the same shape of agent loop you know from cloud
  coding agents, running against a model you control.
- **Approval-gated mutations.** Every file write, edit, and shell command is
  gated behind explicit approval by default. Destructive commands are hard-blocked
  even in `--yolo` mode.
- **Model-agnostic.** Works with tool-capable models (native `tool_calls`) *and*
  models without a tool template, via a prompt-based fallback protocol.

## Key features

- **One agent loop, dispatched over local tools** — `build prompt → call model →
  parse action → approve if mutating → run tool → feed observation back → repeat`.
- **Dual tool-call protocol** — native `tool_calls` for models with a tool
  template; a `#!json ```action {…} ``` ` fallback for models without one.
  Auto-detected per model.
- **Context compaction** — folds the oldest steps into a recap when the
  conversation exceeds the context budget, keeping the system prompt, goal, and
  recent turns verbatim.
- **Model routing** — auto-picks a model per turn by role (code / language /
  router) using a cheap heuristic plus a tiny classifier.
- **Hardware-aware tuning** — sets `max_steps` and context budget from available
  RAM.

## High-level architecture

Revenant is built on a small stack of pip packages with an acyclic dependency
graph:

```text
nerva-core  ──►  nerva-agent  ──►  revenant-cli
(LLM + storage   (the agent        (the `revenant`
 + profiles)      engine + tools)    command)
```

| Package | Role |
|---------|------|
| **nerva-core** | Shared foundation: the local-LLM layer, SQLite storage, profiles, and memory (stdlib-only). |
| **nerva-agent** | The agent **engine**: the loop, the tools, the tool-call protocol, model routing, and capacity tuning. |
| **revenant-cli** | The `revenant` command — a thin front-end that wires the engine to a coding tool registry. |

See [Architecture](architecture.md) for the full component and data-flow breakdown.

---

## Next steps

<div class="grid cards" markdown>

- :material-rocket-launch: **[Quickstart](getting-started.md)** — get a working
  `revenant` command and run your first task in under 5 minutes.
- :material-download: **[Installation](installation.md)** — detailed setup for
  local, virtualenv, and standalone-binary installs.
- :material-book-open-variant: **[Guides](guides/index.md)** — task-oriented
  how-tos for everyday workflows.
- :material-console: **[CLI reference](reference/cli.md)** — every flag and
  argument.

</div>

# Revenant CLI

**Revenant** is the local coding-agent CLI — a Claude-Code-style agent powered
entirely by your local Ollama model. It reads and searches your codebase and
answers a goal by running a real tool-calling loop. Fully offline.

Entry point: [`apps/agent_cli.py`](api/agent_cli.md). Engine: [`AgentLoop`](api/agent_loop.md).

## Usage

```bash
# one-shot goal in the current directory
python3 apps/agent_cli.py "summarize what core/ does"

# point at another repo, pick a model, cap steps
python3 apps/agent_cli.py --workspace ~/proj --model qwen2.5:7b --max-steps 10 \
  "where is authentication handled?"
```

| Flag | Default | Meaning |
|---|---|---|
| `goal` | — | What you want the agent to do (positional). |
| `--workspace` | `.` | Repo root the agent may read. **All file access is confined here.** |
| `--base-url` | `http://localhost:11434` | Local model server. |
| `--model` | `code` role | Override the model (default resolves the `code` role via the router → `qwen2.5:7b`). |
| `--max-steps` | auto | Loop step cap (0 = derived from detected hardware). |
| `--max-context-tokens` | auto | Compact once the transcript exceeds this budget (0 = derived from hardware). |
| `--read-only` | off | Disable mutating tools; investigate only. |
| `--yolo` | off | Auto-approve mutating tools (skips the prompt; footgun guards still apply). |
| `--no-native-tools` | off | Force the prompt-based protocol (default: auto-detect per model). |
| `--no-color` | off | Plain output. |

## What it can do

**Read-only tools** (always available, confined to the workspace root):

- `read_file(path)` — read a text file (capped to keep context sane).
- `list_dir(path)` — list a directory.
- `glob(pattern)` — find files by glob (`**/*.py`, `core/*.py`).
- `grep(pattern, path?)` — regex search (uses ripgrep when available).

**Mutating tools** (enabled unless `--read-only`; each pauses for approval):

- `write_file(path, content)` — create or overwrite a file.
- `edit_file(path, old, new)` — replace exactly one occurrence of `old` (errors on
  0 or >1 matches, so the model must give unambiguous context).
- `run_bash(command)` — run a shell command in the workspace (builds, tests, git…).

## How a run works

The loop (`AgentLoop.run`) is: call the model → [`parse_action`](api/agent_protocol.md)
the reply (native `tool_calls` on qwen2.5, or a prompt-based ` ```action ` block) →
**approve** (if the tool mutates) → dispatch the tool → feed the observation back →
repeat until the model gives a final answer or `--max-steps` is hit. Each step
streams to the terminal:

```
revenant · model=qwen2.5:7b · workspace=/Users/you/proj
→ read_file(path='core/agent_router.py')
  """Task-based multi-model routing for the local agent harness. …
core/agent_router.py implements a task-based multi-model routing system …
```

## Hardware-aware defaults

`revenant` detects the machine (RAM, cores, platform) and the active model's size,
then picks sensible defaults for `--max-steps` and `--max-context-tokens` — a bigger
machine gets a larger context budget and step cap. It prints the profile on startup:

```
capacity: 24GB RAM, 15 cores, Apple Silicon, model 4.4GB → context 6000, steps 15, keep models resident
```

Any explicit flag overrides the recommendation. See [`agent_capacity`](api/agent_capacity.md).

**Native tool-calling is auto-detected per model.** Tool-capable models (qwen2.5)
use the structured `tool_calls` path; models with no tool template (Stheno) fall back
to the prompt-based ` ```action ` protocol automatically. The result is probed once
and cached ([`agent_native_tools`](api/agent_native_tools.md)); `--no-native-tools`
forces the prompt-based path.

## Context management (long runs)

Local 7B/8B models have a limited context window, so a long investigation would
eventually overflow it. The loop keeps its running transcript under
`--max-context-tokens`: once exceeded, the **oldest** step-pairs (an action plus its
observation) are folded into a short recap turn, while the **system prompt, the
goal, and the most recent few steps are always kept verbatim**. This is the loop's
local analog of context compaction — it prints `[context: compacted N old turns]`
when it fires. Raise the budget for models with a larger window; lower it to force
earlier compaction on tight hardware.

## Safety (development-oriented, not content censorship)

This is an on-prem private dev tool: it does **not** refuse development tasks. The
guardrails that remain are correctness/damage guards, not content filters:

- **Human approval gate** — every mutating tool (`write_file`, `edit_file`,
  `run_bash`) pauses and shows you what it will do (the content, an old/new diff, or
  the command) and waits for `y/N`. `--yolo` auto-approves; `--read-only` removes
  the tools entirely.
- **Path confinement** — every file tool resolves under `--workspace` and rejects
  `..`, absolute paths, and symlinks that escape the root.
- **Destructive-command footgun block** — `run_bash` hard-refuses a small set of
  catastrophic patterns (`rm -rf /`, fork bombs, `mkfs`, `dd of=/dev/…`, `shutdown`,
  …) **even under `--yolo`**. Workspace-relative deletes like `rm -rf ./build` are
  allowed — this blocks disasters, not development.

When approving an `edit_file`, you see the exact `old` → `new` spans; when approving
`write_file`, the file content; when approving `run_bash`, the command line.

## Design records

- [ADR 0003 — Local Agent Harness](adr/0003-local-agent-harness.md)
- [Agent Harness Plan](agent-harness-plan.md)

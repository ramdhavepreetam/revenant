# Architecture

How Revenant is put together: the packages, the agent loop, and the key design
decisions.

---

## Components

Revenant is a stack of pip packages with a strictly **acyclic** dependency graph:

```text
nerva-core  ──►  nerva-agent  ──►  revenant-cli
```

| Package | Responsibility | Depends on |
|---------|----------------|------------|
| **nerva-core** | The shared foundation — local-LLM layer, SQLite storage, profiles, and memory. Standard-library only. | — |
| **nerva-agent** | The agent **engine** — the loop, tools, tool-call protocol, model routing, and capacity tuning. | nerva-core |
| **revenant-cli** | The `revenant` command — wires the engine to a coding tool registry with `role=code`. | nerva-core, nerva-agent |

!!! note "One engine, thin front-end"
    All the intelligence lives in `nerva-agent`. `revenant-cli` is a thin
    front-end: it builds a tool registry, sets the role, and runs the loop.

## The agent loop

The heart of Revenant is `nerva_agent.agent_loop.AgentLoop.run(goal)`:

```text
build system + messages
        │
        ▼
   call the model  ◄─────────────┐
        │                        │
        ▼                        │
  parse the action               │
        │                        │
        ├─ mutating? ─► approve ─┤
        │                        │
        ▼                        │
   dispatch the tool             │
        │                        │
        ▼                        │
 feed observation back ──────────┘
        │
        ▼
final answer / max_steps reached
```

1. **Build prompt** — system instructions + goal + prior turns.
2. **Call model** — via the local Ollama server.
3. **Parse action** — native `tool_calls` or the prompt-based fallback.
4. **Approve** — if the tool mutates, pause for the user (unless `--yolo`).
5. **Dispatch** — run the tool, path-confined to the workspace.
6. **Observe** — feed the result back into the conversation.
7. **Repeat** — until a final answer or `max_steps`.

## Data flow

```text
 you ──goal──► revenant-cli ──► AgentLoop ──HTTP──► Ollama (local model)
                    ▲               │
                    │               ▼
              approvals        tool dispatch ──► workspace files / shell
                    ▲               │
                    └── observations ┘
```

Nothing leaves the machine: the model is local, the workspace is local, and
history is stored in a local SQLite file.

## Key design decisions

!!! abstract "ADR 0001 — Offline local LLM interface"
    Revenant targets a **local Ollama server** as its only model backend, so the
    tool needs no API keys and emits no telemetry. Privacy is structural, not a
    setting.

!!! abstract "ADR 0003 — Local agent harness"
    A single tool-using loop powers the whole product. The loop, tools, and
    protocol live in `nerva-agent` so front-ends stay thin.

### Dual tool-call protocol

Not every local model has a tool template. Revenant supports both:

- **Native `tool_calls`** for models with a tool template.
- **Prompt-based `action` blocks** for models without one — with a tolerant
  parser for imperfect small-model output.

Support is **auto-detected and cached per model**. See
[Tools reference](reference/tools.md).

### Context compaction

When a conversation exceeds `max_context_tokens`, `AgentLoop._compact_messages`
folds the oldest steps into a recap, keeping the system prompt, the goal, and the
most recent turns verbatim — a local analog of the compaction you see in cloud
agents.

### Hardware-aware capacity

`agent_capacity` derives `max_steps` and the context budget from available RAM,
so Revenant adapts to the machine it runs on. Both are overridable per run.

---

## Next steps

- [Tools reference](reference/tools.md)
- [Configure model routing](guides/model-routing.md)
- [Deployment](deployment.md)

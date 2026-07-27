# Companion Agent (front-end)

The Revenant harness powers **two front-ends from one loop**: the
[coding CLI](revenant-cli.md) and the **companion agent**. Both use the same
[`AgentLoop`](api/agent_loop.md), protocol, and parser — only the **tool set** and
**model** differ. That's the whole point of the shared harness.

| | Coding front-end (`revenant`) | Companion front-end (`/api/agent`) |
|---|---|---|
| Model | `qwen2.5:7b` (`code` role, native tools) | Stheno-8B (`companion` role, prompt-based) |
| Tools | read/glob/grep + write/edit/bash | memory_search / memory_save / set_reminder |
| Filesystem/shell | yes (approval-gated) | **no** |
| Approval | y/N on mutating tools | auto (memory tools are low-risk) |

## Endpoint

`POST /api/agent` runs an agentic companion turn:

```jsonc
// request
{ "message": "remember I love hiking in the mountains",
  "companion_profile": "eros", "max_steps": 5 }

// response
{ "reply": "Locked it in — I won't forget your mountain hikes.",
  "tool_activity": [ { "tool": "memory_save",
                       "args": { "category": "preference",
                                 "content": "The user enjoys hiking in the mountains." } } ],
  "steps": 2, "stopped_reason": "final",
  "config": { "role": "companion", "model": "…Stheno…", "companion_profile": "eros" } }
```

This lets the companion take real actions mid-conversation — recall a fact, remember
something new, or set a reminder — instead of only producing prose.

## Tools ([`agent_companion_tools`](api/agent_companion_tools.md))

- `memory_search(query)` — recall relevant remembered facts (read-only; reuses the
  semantic recall index).
- `memory_save(category, content)` — record a fact. Categories: `identity_fact`,
  `preference`, `voice_preference`, `story_fact`, `companion_style`,
  `relationship_state`, `need`, `boundary`.
- `set_reminder(text)` — note something to bring up later (stored as an episode).

There are **no filesystem or shell tools** — the companion registry is a strict subset.

## The boundary safety gate

A wrongly-admitted **boundary** in a companion is a real risk, so `memory_save`
never auto-activates one. A `boundary` write is stored as **`pending`** (held for
the user to confirm) and is **not** added to the recall index; every other category
is written **active**. This mirrors the existing advisor rule in
`aibot_personal_memory.learn` — the gate lives *inside* the tool, so it holds no
matter how the loop reaches it.

Verified live: asking the companion to remember a boundary results in a `pending`
memory and a reply that explicitly waits for confirmation before treating it as real.

## Why Stheno runs prompt-based

Stheno-8B ships no tool template, so native `tool_calls` don't work on it (confirmed
in [ADR 0003](adr/0003-local-agent-harness.md)). The companion front-end therefore
runs with `use_native_tools=False`, and the model emits ` ```action ` blocks that
[`parse_action`](api/agent_protocol.md) reads — the same universal fallback that makes
the harness model-agnostic.

## Reuse (no web dependency)

`build_companion_tools(memory, personal_memory, store, companion_id)` takes the
already-constructed stores (from `web_app`'s `STATE`), so `agent_companion_tools`
imports nothing from `web_app` and is unit-tested with fakes.

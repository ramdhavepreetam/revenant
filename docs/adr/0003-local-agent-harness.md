# ADR-0003 — Local agent harness (one loop, two front-ends)

- **Status:** Implemented
- **Phase:** P0 (foundation) · **F-slices:** F1–F7 + routing + capacity
- **Date proposed:** (backfilled 2026-07-30) · **Date implemented:** (foundational)
- **Depends on:** ADR-0001, ADR-0002 · **Blocks:** ADR-0004..0010

## Context
A single tool-using loop should power the whole product so front-ends stay thin
and behavior is consistent. The loop must work on **both** local models with a
native tool template (qwen2.5) **and** models without one (prompt-only).

## Decision
One `AgentLoop` in `nerva-agent` drives a goal to completion (or a step cap)
over a `ToolRegistry`. Front-ends (coding CLI; the private companion) differ
only in their tool set, not their loop.

### Key sub-decisions
1. **Dual tool-call protocol.** `agent_protocol.parse_action` handles native
   `tool_calls` and prompt-based ```action blocks. Support is auto-detected and
   cached per `(base_url, model)` in `agent_native_tools.supports_native_tools`.
2. **Pluggable tools.** `Tool{name, description, params, run, parallel_safe,
   requires_approval, mutating}`; mutating ⇒ requires_approval by default.
   Registry renders both a prompt doc block and the native schema.
3. **Approval gate.** Before any `requires_approval` tool runs, an `approve`
   hook is consulted (unless `auto_approve`/yolo). Tool-internal damage guards
   (e.g. bash footgun block) are independent of this gate.
4. **Lifecycle hook.** `before_tool(tool_name, args)` fires right before a
   mutating tool runs — used by undo checkpointing (ADR-0010). Hook errors never
   block the tool.
5. **Multi-model routing** (`agent_router`): route each turn to the best local
   role model (code / language / companion / summary), cheap heuristic first,
   one constrained LLM classification only when ambiguous.
6. **Hardware-aware capacity** (`agent_capacity`): tune `max_context_tokens`,
   `max_steps`, `keep_recent_steps`, and residency to the machine.
7. **Context compaction** (`AgentLoop._compact_messages`): fold oldest steps
   into a recap when the transcript exceeds `max_context_tokens`; keep system
   prompt, goal, and the most recent turns verbatim.
8. **Transcript threading.** `AgentResult.messages` is returned so a driver (the
   REPL) can feed history back into the next `run()` — the seam P5/P6 build on.

## Design detail — the loop shape
```
build system + messages (preamble + project doc grounding, F6)
repeat up to max_steps:
    msg    = call_model_message(config, messages, tools=native_schema?)
    action = parse_action(msg)            # native OR prompt-based
    if action is FinalAnswer: done
    approval gate (requires_approval & not auto_approve)
    before_tool(action)                   # snapshot for undo if mutating
    obs = registry.dispatch(action)
    append assistant + observation; compact if over budget; continue
```

## Consequences for later phases
- **MCP (P3)** and **Skills (P4)** add `Tool`s to the registry — zero loop
  changes.
- **Loops (P5)** are a thin driver over `run(goal, history=…)`.
- **Sub-agents (P8)** are the loop instantiating itself with a scoped registry.

## Progress log
- 2026-07-30 — Backfilled from `agent_loop.py`, `agent_tools.py`,
  `agent_router.py`, `agent_protocol.py`, and architecture docs.

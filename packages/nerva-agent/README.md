# nerva-agent

The **agent engine** behind [Revenant](https://github.com/ramdhavepreetam/revenant): a
model-agnostic, tool-calling loop over local LLMs. Provides the loop, the tool registry, a
dual tool-call protocol (native `tool_calls` + a prompt-based fallback for models without a
tool template), read/write/edit/bash tools, task-based model routing, and hardware-aware
capacity tuning.

```bash
pip install nerva-agent
```

## What's in it

- `agent_loop` — `AgentLoop.run(goal)`: call → parse action → (approve) → dispatch → observe → repeat.
- `agent_tools` / `agent_protocol` — tool registry + `parse_action` (native + prompt-based).
- `agent_fs_tools` / `agent_edit_tools` / `agent_bash_tool` — path-confined, approval-gated tools.
- `agent_router` — route each turn to the best local model per role.
- `agent_capacity` / `agent_native_tools` — hardware detection + native-tool auto-detection.

Depends on [`nerva-core`](https://pypi.org/project/nerva-core/). Powers
[`revenant-cli`](https://pypi.org/project/revenant-cli/).

## License

MIT.

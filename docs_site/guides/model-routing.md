# Configure model routing

Revenant can use different models for different jobs and pick the right one per
turn. This guide covers roles, routing, and the native-vs-prompt tool protocol.

!!! note "Prerequisites"
    Revenant [installed](../installation.md); Ollama running with your chosen
    models pulled.

---

## Roles, not just one model

Instead of hardcoding a single model, Revenant maps **roles** to models. Set them
in `.aibot/profiles.json`:

```json title=".aibot/profiles.json"
{
  "model_roles": {
    "code": "qwen2.5-coder:7b",
    "language": "qwen2.5:14b",
    "router": "qwen2.5:7b"
  }
}
```

| Role | Used for |
|------|----------|
| `code` | The main coding driver — reading, writing, editing code. |
| `language` | General reasoning and discussion turns. |
| `router` | A tiny, fast classifier that decides which role a turn needs. |
| `fallback` | Used when classification is inconclusive (defaults to `language`). |

## How a model is picked per turn

For each turn, the router combines a **cheap heuristic** with a **tiny classifier
model** (the `router` role) to choose the best role, then dispatches to that
role's model. This keeps small, fast models on classification and reserves larger
models for the work that needs them.

```text
turn ──► heuristic + router classifier ──► role ──► role's model ──► answer
```

!!! tip "Override for one run"
    An explicit `--model` beats the `code` role for that invocation:

    ```bash
    revenant --model qwen2.5:14b "Refactor this module."
    ```

## Native tools vs. the prompt-based fallback

Revenant supports two tool-call protocols and auto-detects which a model can use:

=== "Native tool_calls"

    Models with a **tool template** (e.g. `qwen2.5-coder:7b`, `qwen2.5:7b`) emit
    structured `tool_calls`. Revenant uses these directly — the cleanest path.

=== "Prompt-based action fallback"

    Models **without** a tool template emit a fenced action block instead:

    ````text
    ```action
    {"tool": "read", "args": {"path": "src/app.py"}}
    ```
    ````

    Revenant parses this fallback, tolerating sloppy 8B output (single quotes,
    mixed quotes, `arguments`/`args` aliases, double-wrapping).

Detection is **cached per model**. Force the fallback with `--no-native-tools`:

```bash
revenant --no-native-tools --model my-custom-model "..."
```

!!! note "After re-pulling a model"
    If you re-pull a model and its tool support changes, clear the cached
    detection so Revenant re-probes it (see
    [`agent_native_tools`](../reference/tools.md)).

## Hardware-aware tuning

Revenant sizes `max_steps` and the context budget from available RAM. Override
either explicitly:

```bash
revenant --max-steps 20 --max-context-tokens 8000 "..."
```

When the conversation exceeds the context budget, Revenant **compacts** — folding
the oldest steps into a recap while keeping the system prompt, the goal, and the
most recent turns verbatim.

---

## Next steps

- [Configuration](../configuration.md) — the full settings picture.
- [CLI reference](../reference/cli.md) — `--model`, `--no-native-tools`, `--max-steps`.
- [Architecture](../architecture.md) — how routing fits the agent loop.

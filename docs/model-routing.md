# Model Routing

AIBot routes each turn to the **best local model for the kind of work**, instead of one
model doing everything — a local "mixture-of-experts by role." Implemented in
[`core/agent_router.py`](api/agent_router.md); design record: [ADR 0003](adr/0003-local-agent-harness.md).

## Roles

| Role | Model (default) | Used for |
|---|---|---|
| `code` | `qwen2.5-coder-abliterate:14b` | Writing, editing, debugging, reading code; tool use. Code-specialized, uncensored, native tool-calling. |
| `language` | `qwen2.5:14b` | Discussion, explanation, reasoning, brainstorming, advice. |
| `companion` | Stheno-8B (GGUF) | Persona / roleplay / emotional conversation. |
| `summary` | `gemma:latest` | Rolling session summaries (see `core/aibot_summary.py`). |
| `router` | `qwen2.5:7b` | The tiny classifier itself (constrained one-word output — kept small/fast). |

The **`code`** role uses `huihui_ai/qwen2.5-coder-abliterate:14b` — Qwen's code-specialized
line (much stronger at code than general `qwen2.5`), abliterated so it doesn't refuse
development tasks. Note: this GGUF build does **not** carry a native tool template, so the agent
loop auto-detects that ([`agent_native_tools`](api/agent_native_tools.md)) and drives it via the
prompt-based ` ```action ` protocol — which works reliably in practice. General `qwen2.5:7b`
(which *does* have native tool-calling) remains available as a fast/light coding model via
`revenant --model qwen2.5:7b`.

Roles map to **model-profile names** in `profiles.json` → `model_roles`, which resolve to
`{backend, base_url, model}` via the existing `models` section — so endpoint data is never
duplicated.

```json
"model_roles": {
  "code": "qwen2.5-coder-14b",
  "language": "qwen2.5-14b",
  "companion": "stheno-8b",
  "summary": "gemma",
  "router": "qwen2.5-7b",
  "fallback": "language"
}
```

## How a turn is routed

The auto-router runs inside `_prepare_turn` (`apps/web_app.py`), right after the companion
flag is known, and **mutates the turn's `ChatConfig` in place** — so both the blocking
(`call_model`) and streaming (`stream_model`) paths use the routed model:

1. **Companion turn?** → force `role = "companion"` (Stheno). The classifier is skipped
   entirely — companion mode owns its turn.
2. **Otherwise → `classify(user_text)`**:
    - **Heuristic pre-filter** (`_heuristic_role`) resolves obvious turns with **zero LLM
      cost**: code fences / file paths / code keywords → `code`; "explain / why / discuss /
      compare" → `language`.
    - **Only genuinely ambiguous turns** hit the `router` model — one constrained,
      one-word classification call (~0.2s; on `qwen2.5:7b`, which is usually already resident
      for the `code` role, so no model swap).
3. `config_for_role(role, …, base=config)` swaps `model`/`backend`/`base_url` on the turn's
   config, preserving the tokens/temperature/system-prompt the turn already computed.

Any failure (router model missing, classification error) **degrades to the `fallback` role
and never breaks the turn.**

## Memory footprint & model swapping

Models are **swapped on demand** — AIBot does not pin every model resident. Ollama's default
`keep_alive` unloads idle models; switching to a not-resident model pays a one-time ~1–3s
load. Because the `router` and `code` roles share `qwen2.5:7b`, coding sessions rarely swap.
The expensive case is companion → language (Stheno → 14B). An optional `warm_role()` preflight
can hide part of that latency (not enabled by default).

Sized for a 24 GB machine (Apple M5 Pro): a 7B + a 14B + an 8B + gemma cannot all sit resident
comfortably, so swap-on-demand is the deliberate default.

## The response tells you which brain answered

`/api/chat` (and the streaming variant) return the chosen role and model in the response
`config` block:

```json
"config": {
  "model": "qwen2.5:14b",
  "role": "language",
  "role_model": "qwen2.5:14b",
  "response_shape": "balanced",
  "response_mode": "chat"
}
```

## Opting out (legacy single-model behavior)

Set `route_models: false` in the request body to disable routing entirely — the turn uses
whatever model `build_config` resolved (request body / `model_profile` / default), exactly as
before this feature existed. If `model_roles` is absent from `profiles.json`, or a role's model
isn't pulled, routing also degrades to legacy behavior. `/api/chat` never breaks.

## Adding a new role or changing a model

1. Add a model profile under `profiles.json` → `models` (or `DEFAULT_PROFILES["models"]` in
   `core/aibot_profiles.py`): `{"backend", "base_url", "model", "notes"}`.
2. Point a role at it under `model_roles`.
3. Pull the model (`ollama pull <name>`).

No code change is needed to re-point an existing role. To add a *new* classified role, extend
`ROUTER_ROLES` and the classifier prompt in `core/agent_router.py`.

## Reuse by the agent harness

`core/agent_router.py` imports only `local_llm_writer` (never `web_app`), so the coming
[agent harness](agent-harness-plan.md) reuses it directly: the coding front-end forces
`role="code"`; a discussion/planning step can call `config_for_role("language", …)` to swap
to the 14B for a single call.

## API

See the full generated reference: [`core.agent_router`](api/agent_router.md).

Key entry points:

- `classify(user_text, *, has_companion=False, base_url=…, profiles=…) -> str`
- `config_for_role(role, base_url, profiles, *, base=None) -> ChatConfig | None`
- `warm_role(role, base_url, profiles) -> None` (optional preflight)

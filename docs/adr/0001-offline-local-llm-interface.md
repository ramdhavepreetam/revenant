# ADR-0001 — Offline local LLM interface

- **Status:** Implemented
- **Phase:** P0 (foundation) · **F-slices:** pre-F
- **Date proposed:** (backfilled 2026-07-30) · **Date implemented:** (foundational)
- **Depends on:** — · **Blocks:** every other ADR

## Context
Revenant is positioned as "Claude Code, but for your private LLM." The single
most important product constraint is **privacy is structural, not a setting**:
no code lives, or edits, leaves the machine. This decision predates and
constrains everything else.

## Decision
Target a **local Ollama server** (default `http://localhost:11434`) as the only
model backend. No cloud SDKs, no API keys, no telemetry. All model calls go
through one thin layer, `nerva_core.local_llm_writer`, exposing `ChatConfig`,
`call_model`, `call_model_message`, `estimate_tokens`, and `LocalLLMError`.

Rejected: a provider-abstraction that could reach OpenAI/Anthropic. Even as an
option it would undermine the privacy guarantee and complicate the threat model.

## Design detail
- `ChatConfig` carries `{backend, base_url, model, temperature, top_p,
  repeat_penalty, min/max_tokens, context_messages, system_prompt}`.
- `estimate_tokens` uses tiktoken `cl100k_base` when available, falling back to a
  word-count approximation on minimal offline installs (F4).
- Every higher layer (router, loop, tools) imports only this module for model
  access — never a cloud client.

## Consequences
- Any new network capability must be local (Ollama) or a **user-configured local
  server** (e.g. an MCP server in P3). This is the offline invariant restated.
- Model-capability differences (native tool-calls vs none) become a first-class
  concern → handled by the dual protocol (ADR-0003).

## Progress log
- 2026-07-30 — Backfilled from code references (`agent_router.py` cites "ADR
  0001") and `docs_site/architecture.md`.

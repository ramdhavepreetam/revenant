# nerva-core

The offline **local-LLM layer** for the [Revenant](https://github.com/ramdhavepreetam/revenant)
toolchain: a thin client for Ollama / OpenAI-compatible local servers, plus SQLite-backed
conversation storage, model profiles, and a local memory index. Pure standard library — no
third-party dependencies.

```bash
pip install nerva-core
```

## What's in it

- `local_llm_writer` — `ChatConfig`, `call_model`, `call_model_message` (native tool_calls),
  `stream_model` — for Ollama and OpenAI-compatible backends.
- `aibot_storage` — SQLite conversation store (source of truth) + summaries + episodes.
- `aibot_profiles` — model / style / generation profiles and role resolution.
- `aibot_memory` — a local semantic-recall adapter.

Used by [`nerva-agent`](https://pypi.org/project/nerva-agent/) (the agent engine) and
[`revenant-cli`](https://pypi.org/project/revenant-cli/) (the coding CLI).

## License

MIT.

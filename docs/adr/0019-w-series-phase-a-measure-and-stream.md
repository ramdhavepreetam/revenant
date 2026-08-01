# ADR-0019 — W-series Phase A: measure, then stream (W0/W1/W2)

- **Status:** Accepted — implementation starting with W0
- **Phase:** W-series (0.6.0) Phase A · **W-slices:** W0 eval metrics + rename tasks
  + regression gate · W1 stream plain-content assistant text · W2 tool-call turns
  under streaming + live render
- **Date proposed:** 2026-08-01 · **Date implemented:** —
- **Depends on:** ADR-0018 (W-series strategy), ADR-0015 (eval harness — W0
  extends `evals/`), ADR-0017 (event model — W1/W2 add a `token` kind + stream
  through the TUI/console sinks) · **Relates to:** ADR-0001 (offline — streaming is
  local Ollama only), ADR-0011 (W0 is how the streaming lift is *proven*)

## Context
Phase A delivers the flagship "responsiveness" theme (streaming) plus the
measurement backbone that proves it. Verified in code:

- **Streaming transport exists, orphaned.** `stream_model(config, messages)`
  (`local_llm_writer.py:214-283`) yields text deltas for Ollama (NDJSON) and
  OpenAI-compat (SSE) and its own docstring says the **caller** must buffer the
  full text and apply the same post-processing — i.e. it was designed to be wired
  by a caller that does exactly what W1 needs. It has **zero call sites**.
- **The agent call is non-streaming.** `AgentLoop.run` calls `call_model_message`
  (`agent_loop.py:325`) → `call_ollama_message`/`call_openai_message`, both
  hardcoded `"stream": False` (`local_llm_writer.py:157,182`), returning the whole
  assistant message dict at once because it reads `message["tool_calls"]`.
- **Events are additive + string-kinded.** `AgentEvent.kind` is a documented string
  set (`agent_loop.py:58-60`); adding `"token"` is additive exactly like V0 added
  `agent`/`context` (`agent_loop.py:66-71`). `_emit` at `:160`; sinks are
  `_on_event` (`cli.py:129-140`) and the TUI `ActivityLog`.
- **The eval harness is thin.** `ScoreResult` is `passed`/`detail` only
  (`evals/tasks/base.py:22-35`); `run.py` aggregates pass-rate + wall-time over 5
  tiny single-file Python tasks (`tasks/__init__.py`), supports `--repeat`/
  `--compare`, but has no richer metrics and no regression gate.

## Decision
Ship W0 first (it scores W1/W2 and all of Phase B/C), then W1 (content streaming),
then W2 (tool-call turns under streaming). All three are additive; the plain and
rich consoles keep byte-parity when they ignore the new `token` kind.

### W0 — eval metrics + rename tasks + regression gate
- **Metrics.** Extend `ScoreResult` (`base.py:22-35`) — or add a sibling `Metrics`
  record carried alongside it — with optional `steps: int`, `tokens: int`,
  `edit_precision: float` (fraction of the agent's edits that survived to the final
  workspace vs. were reverted/overwritten). All default-None/0 so existing tasks
  and `tests/test_evals.py` are unaffected.
- **Aggregate + compare.** Extend `Report`/`TaskOutcome` (`run.py`) to carry and
  JSON-round-trip the new fields, and `compare_reports` to diff them (so
  `--compare pre.json post.json` shows Δsteps / Δtokens / Δprecision, not just
  Δpass-rate).
- **Rename tasks.** Add 3–5 tasks to `tasks/__init__.py` targeting **project-wide
  rename across multiple call sites** — the exact profile W4 will be scored on
  (multi-file fixtures, scored by `run_pytest` on hidden tests that import the new
  name). These fail pre-W4, pass post-W4c.
- **Regression gate.** Extend `run.py`'s exit-code logic to "must not regress below
  a saved baseline report" (opt-in `--gate baseline.json`), so CI can fail on a
  drop, not only on <100%.
- **Reuses:** the injectable `AgentRunner` Protocol + temp-dir isolation +
  `--compare` (`run.py`); the `run_pytest` scorer helper (`base.py:61`); the
  `_FakeAgentRunner` model-free test path.

### W1 — stream plain-content assistant text
- Add `"token"` to the `AgentEvent.kind` doc set + note (`agent_loop.py:58-71`).
  A `token` event carries the incremental `text` delta (and `agent`/`step` like any
  event); no new field needed.
- At the model-call seam (`agent_loop.py:325`), when the turn is content-only (first
  pass: the final answer path), call `stream_model` instead of `call_model_message`,
  emit a `token` event per delta, and **buffer** the full text so the existing
  post-call logic (`parse_action`, transcript append) is byte-identical.
- **Reuses:** `stream_model` (`local_llm_writer.py:214`); `_emit`; the console/TUI
  sinks already consume events, so `token` is additive.

### W2 — tool-call turns under streaming + live render
- The hard part: native `tool_calls` arrive as a whole message. Approach: **stream
  the assistant's leading content deltas for UX, buffer, then read
  `message["tool_calls"]` from the completed message and dispatch exactly as today**
  (`agent_loop.py` dispatch block). No partial tool-call JSON parsing.
- Wire the console `_on_event` (`cli.py:129`) and the TUI `ActivityLog` to render
  `token` events live (spinner → streaming text, replacing the block-at-once view).
- **Reuses:** the entire approval / `before_tool` / `after_tool` / dispatch path is
  unchanged — streaming feeds only the content render, never the tool path; TUI
  worker-thread streaming from V-series.

### Failure & degradation
- Any streaming error (transport, partial read) → fall back to the non-streaming
  `call_model_message` for that turn; the run continues. Guarded like every optional
  path (ADR-0001/invariant 4).
- A `token`-ignoring consumer (plain console when not rendering deltas; old tests)
  sees byte-identical output — the final `assistant`/`final` event still carries the
  whole text.

## Test plan (model-free / offline; CI runs bare `pytest`)
- **W0** `tests/test_evals.py`: new metrics aggregate correctly through
  `_FakeAgentRunner` (steps/tokens/edit-precision); `--compare` reports their
  deltas; JSON round-trips; the new rename tasks fail pre-fix / pass post-fix
  (real fixtures + `run_pytest`, not mocked scoring); `--gate` returns non-zero
  below baseline, zero at/above.
- **W1** `test_agent_loop.py` (streaming): inject a fake `stream_model` yielding
  known deltas; assert N `token` events whose concatenation == the buffered content,
  `AgentResult.answer` byte-identical to the non-streaming path, and a
  `token`-ignoring consumer sees unchanged output (byte-parity).
- **W2** streaming with tools: fake stream yields content deltas then resolves to a
  message *with* `tool_calls`; assert the loop dispatches the tool identically to
  today, `token` events fired for the content prefix, and the approval gate + undo
  snapshot still fire. Cover a pure-content turn and a tool turn.
- **Fallback:** a `stream_model` that raises → the turn falls back to
  `call_model_message` and the run completes (monkeypatched).

## Acceptance criteria
- [ ] `evals/run.py` records step-count, token-cost, and edit-precision per task;
      `--compare` diffs them; `--gate baseline.json` fails CI on a regression.
- [ ] 3–5 project-wide-rename tasks exist and fail against a no-op agent (ready to
      score W4).
- [ ] `revenant chat`/TUI streams the assistant's answer token-by-token; the final
      answer is byte-identical to the non-streaming path.
- [ ] A turn that ends in a tool call still dispatches, approves, and undoes exactly
      as before — streaming changed only the content render.
- [ ] Plain console (ignoring `token`) keeps byte-parity; suite green (bare
      `pytest`), ADR-0019 + README updated. Streaming failure falls back cleanly.

## Open questions
- **Streaming scope for v1:** stream only the *final* content answer, or every
  content-only assistant turn? Start with the final answer (biggest perceived win,
  smallest blast radius), widen if it's clean.
- **`edit_precision` definition:** "edits that survived to the final workspace" —
  measured by diffing the agent's cumulative edits against the final tree. Confirm
  the exact denominator (edits attempted vs. edits that touched a real line) during
  W0 implementation.

## Progress log
- 2026-08-01 — Proposed + Accepted. Phase-A spec written before code (per ADR-0018
  / the series workflow). W0 implementation starts next: the measurement backbone
  that scores every later slice.

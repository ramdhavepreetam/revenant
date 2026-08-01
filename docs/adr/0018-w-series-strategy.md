# ADR-0018 — W-series: faster to watch, deeper, and measurably better (strategy)

- **Status:** Implemented (all phases) · released v0.6.0 · phase ADRs 0019 (A) /
  0020 (B) / 0021 (C) track the slices below
- **Phase:** W-series (0.6.0) · **W-slices:** W0 eval metrics+gate, W1 content
  streaming, W2 tool-turn streaming, W3 `--every`+graph cache, W4a `replace_all`+
  single-file rename, W4b `ToolParam` array type, W4c multi-file `apply_edits`,
  W5 role-routed sub-agents, W6 `mcp add`+HTTP/SSE
- **Date proposed:** 2026-08-01 · **Date implemented:** —
- **Depends on:** ADR-0015 (eval harness — W0 extends it), ADR-0017 (event model +
  TUI — W1/W2 stream through it), ADR-0011 (harness-carries-the-model — W0 is how
  we *prove* the lift), ADR-0008 (code graph — W3/W4 build on it), ADR-0009
  (sub-agents + git-undo — W5 routes them, W4c reverts through undo), ADR-0004 (MCP
  — W6 extends transport) · **Relates to:** ADR-0001 (offline — streaming and MCP
  HTTP target local servers only), ADR-0002 (placement — lowest package holds each
  change)

## Context
P0–P8 built the feature surface; the H-series made a weak local model reliable
(verify→repair, context injection, decompose); the U-series made the CLI usable;
the V-series (0.5.0, just released) made it an interactive Claude-Code-like
terminal. **0.5.0 was the last planned phase.** What remains, found by reading the
code (not assumed), falls into three coherent themes:

1. **The agent isn't live to watch.** ADR-0017 explicitly deferred token-by-token
   streaming: agent events are per-step / post-hoc (`agent_loop.py:_emit`), so the
   TUI and REPL show a spinner then a finished block, not text as it generates.
   A working streaming transport `stream_model` already exists
   (`local_llm_writer.py:214-283`) but has **zero call sites** — built for chat,
   never wired to the agent loop.
2. **Capability gaps limit what it can safely do.** The code graph knows every
   call site (`code_graph/tools.py`) but `edit_file` replaces **exactly one**
   occurrence and errors on >1 (`agent_edit_tools.py:33-54`) — no `replace_all`, no
   multi-file atomic edit, so a project-wide rename is N brittle single edits. The
   graph is rebuilt from scratch in-memory each run (no persistence) despite having
   incremental `reindex_file`/`remove_file` primitives already. Sub-agents clone
   the parent config verbatim despite `config_for_role` routing existing. MCP is
   stdio-only and there is no `mcp add` writer. `loop --every` is missing (though
   `--watch` shipped).
3. **We can't measure any of it.** The H0 eval harness (`evals/run.py`) works but
   records **only binary pass/fail + wall-time** over **5 tiny tasks**, with no
   regression gate. Every improvement above claims a lift (time-to-first-token,
   rename pass-rate, edit-precision) that is unprovable until the harness records
   it.

## Decision
Ship the **W-series (0.6.0)** across all three themes, sequenced so **measurement
lands first and scores everything after it**, low-risk wins precede the hard
streaming/refactor work, and every new mutating capability rides the existing
approval + undo + verify machinery. Governing choices:

- **Measure before you move (W0 first).** Extend the eval harness with richer
  metrics (step-count, token-cost, edit-precision) + project-wide-rename tasks + a
  regression gate *before* the capability slices exist, so each later slice re-runs
  it to prove its delta. Mirrors how H0 anchored the H-series (ADR-0015).
- **Extend the event model, don't fork it (W1/W2).** Add a `token` (delta) event
  kind — additive, exactly like V0 added `agent`/`context`. Stream the content
  prefix for UX; for tool-call turns, **buffer then dispatch tools as today** —
  native `tool_calls` arrive as a whole message (`local_llm_writer.py:157,182`
  hardcode `stream:False`), so we do **not** parse partial tool-call JSON.
- **Close the schema gate as its own slice (W4a→b→c).** `ToolParam` is scalar-only
  by design (`agent_tools.py:35-37`). A multi-file `apply_edits` wants an
  array-of-objects param. So: scalar-safe `replace_all` + single-file rename (W4a)
  → relax `ToolParam` to one array type as a tested engine slice (W4b) → the
  multi-file atomic tool that consumes it (W4c). Sequencing, not a blocker.
- **Reuse the pre-cut seams (W3/W5/W6).** `--every` parallels shipped `--watch`;
  graph persistence calls the existing `reindex_file`/`remove_file`; sub-agent
  routing threads a `role` into the existing `config_for_role`; MCP HTTP reuses the
  transport/url the config already carries.
- **Every mutating capability is approval-gated + undo-covered + verify-checked.**
  `replace_all` and `apply_edits` are `mutating ⇒ requires_approval`
  (`Tool.__post_init__`), snapshot through `before_tool`, verify through
  `after_tool`, and `apply_edits` reverts **atomically** (all-or-nothing) via the
  checkpointer.
- **Rejected:** (a) shipping only Phase A as 0.6.0 and deferring B/C to a later
  series — the user chose the full three-theme series. (b) Relaxing `ToolParam`
  broadly to arbitrary nested objects — we add exactly one array shape, contained.
  (c) Streaming partial tool-call JSON — brittle, out of scope; buffer-then-dispatch
  is enough for a live view.

## Excluded as already-shipped (do NOT re-plan)
Verified in code; the W-slices wire/persist/thread these, they do not rebuild them:
- **`loop --watch`** — `cli.py:769-811` (`_cmd_loop_watch`). Only `--every` remains.
- **`reindex_file`/`remove_file`** — `indexer.py:110-139` exist + are tested; they
  lack a *caller*, *persistence*, and a *staleness check*.
- **`config_for_role`** role routing — `agent_router.py:69-110`. Missing only the
  thread from sub-agent spawn.
- **`stream_model`** — `local_llm_writer.py:214-283` works; missing only call sites.

## Phase roadmap (A / B / C — three PRs, then 0.6.0)

| Phase | ADR | Slices | Theme | Ships |
|-------|-----|--------|-------|-------|
| **A — Measure & Stream** | 0019 | W0, W1, W2 | 3 + 1 | eval metrics/gate; `token` event; streaming loop + live TUI/console render |
| **B — Deeper capability** | 0020 | W3, W4a, W4b, W4c | 2 | `--every`; graph cache; `replace_all`; `ToolParam` array; atomic `apply_edits` |
| **C — Reach** | 0021 | W5, W6 | 2 | role-routed sub-agents; `mcp add` + HTTP/SSE |

**Order rationale:** W0 first (scores the rest); W1→W2 paired (content easy, tool
turns the hard part); W3 the cheap win between hard blocks; W4a→b→c the
highest-leverage refactor ladder carrying the schema gate; W5/W6 self-contained
backlog-closers.

**Release 0.6.0:** bump 0.5.0 → 0.6.0 (deps pinned `>=0.6.0`), changelog
`[Unreleased]` → `[0.6.0]`, ADRs + roadmap README updated, merge, tag `v0.6.0`,
installer CI + PyPI — identical to the 0.5.0 flow.

## Cross-cutting invariants (per ADR-0001/0002, every slice holds)
- **Offline always** — streaming hits local Ollama/OpenAI-compat; W6 HTTP targets a
  user-configured local MCP server (same class as Ollama). No cloud SDK/telemetry.
- **Acyclic deps** — `token` kind + streaming wiring + `ToolParam` in `nerva-agent`;
  `stream_model` transport in `nerva-core`; `--every`, `mcp add`, sub-agent role
  threading, console/TUI render in `revenant-cli`.
- **Degrade gracefully** — corrupt graph cache → full rebuild; streaming failure →
  fall back to `call_model_message`; unknown sub-agent role → verbatim clone; bad
  `mcp add` → clean error.
- **Approval gate sacred + undo** — every new mutating tool routes through
  `requires_approval` / `before_tool` / `after_tool`; `apply_edits` reverts atomically.
- **Docs are done-criteria** — this ADR + phase ADRs 0019–0021, roadmap README
  updated per slice, then the 0.6.0 release notes.

## Progress log
- 2026-08-01 — Proposed + Accepted. Strategy written before code (durable record
  first, per the P-series/H/U/V workflow). Direction confirmed with the user:
  all three themes (streaming + deeper capability + quality) as one full
  three-phase W-series → 0.6.0. Grounded in code exploration: `stream_model`
  orphaned, `edit_file` single-occurrence-only, graph rebuilt each run,
  `ToolParam` scalar-only, eval harness thin. Phase ADRs 0019–0021 to follow;
  implementation begins with W0 (the measurement backbone).

# ADR-0016 — Make the CLI usable: setup UX + rich live console (U-series)

- **Status:** Accepted (strategy) · per-slice U0–U4 track below
- **Phase:** U-series (0.4.0) · **F-slices:** U0 default-model, U1 preflight/errors,
  U2 doctor/models/picker, U3 Console abstraction, U4 reroute chrome
- **Date proposed:** 2026-07-31 · **Date implemented:** —
- **Depends on:** ADR-0002 (placement: terminal UX → revenant-cli), ADR-0003 (loop
  `on_event` seam) · **Relates to:** ADR-0001 (offline — rich is pure rendering)

## Context
Revenant works, but is **hard to use** vs. polished agent CLIs. Two problems,
found by reading the code (not assumed):

1. **Setup friction (higher impact).** No pre-flight that Ollama is up or the
   model is pulled; the first failure is a raw `LocalLLMError` deep in the model
   call with no actionable hint. No model discovery/picker — the user must know
   the exact Ollama model string. The `config` subcommand is a stub. And a real
   **bug**: the default `code` role resolves to
   `huihui_ai/qwen2.5-coder-abliterate:14b` (`aibot_profiles.py`), but the docs
   tell users to pull `qwen2.5-coder:7b` — so a by-the-docs first run fails with
   "model not found."
2. **No live "what's going on" view.** Output is plain `print()` + ANSI; there's
   no coherent console showing the agent thinking / acting / reporting.

## Decision
Build a **richer terminal console + setup UX**. NOT a VS Code fork (greenfield,
no home package, out of scope). Governing choices (confirmed):

- **Render:** `rich` as an **optional dependency** (`revenant-cli[rich]`) with a
  **plain-ANSI stdlib fallback** — auto-use rich if installed + isatty + not
  `--no-color`/`NO_COLOR`, else today's output. Mirrors the optional-`tiktoken`
  pattern (`local_llm_writer._get_encoder`). Keeps zero *required* runtime deps.
- **Default model:** `code`/`router`/`summary` all → `qwen2.5-coder:7b` (one pull,
  first-run works). 14b/`qwen2.5:7b` entries kept for power users.
- **Preflight:** hard-fail with an actionable message + an interactive model
  picker; `--skip-preflight` escape hatch.
- **Console wiring:** build the Console once in `_build_agent`, return it as the
  tuple's 5th slot (was `color`); update the test helpers in lockstep.
- **Installers:** bundle `rich` in the macOS/Windows binaries.

## Key seams (verified in code)
- **Clean render seam:** `AgentLoop` only `_emit`s typed `AgentEvent`s
  (`agent_loop.py:40,137`) via `on_event` (wired `cli.py:430`). Swapping the
  renderer needs **zero `agent_loop.py` changes**; `AgentResult.events` keeps the
  full trace, so moving truncation into the renderer loses nothing.
- **Hardcoded chrome:** `make_printer` (`cli.py:81`), `make_approver`
  (`cli.py:120`; `_preview_args` is a naive block, not a real diff), ~9 dim status
  prints in `_build_agent` (`cli.py:342-421`), per-command banners, REPL prompt.
- **Setup plumbing exists:** the `/api/tags` GET is already in
  `agent_capacity.model_size_gb`; `post_json` raises the connect/404 errors
  (`local_llm_writer.py:54-75`); `_build_agent` (`cli.py:299`) is the pre-flight
  home; the `config` stub is the hook for `doctor`.
- **Tuple contract:** `_build_agent` returns `(ws, config, rec, loop, color)`;
  tests hardcode this 5-tuple (`tests/test_cli.py` `_patch_build_agent*`).

## Phase breakdown (U-series)

### U0 — default-model fix (Phase A)
`aibot_profiles.py` `DEFAULT_PROFILES`: add `qwen2.5-coder:7b`; point
`code`/`router`/`summary` at it. Docs stay consistent. **Test:** assert those
roles resolve to `qwen2.5-coder:7b` (guards re-drift vs docs).

### U1 — preflight + list_local_models + actionable errors + OLLAMA_HOST (Phase A)
- `nerva_core/local_llm_writer.list_local_models(base_url, timeout=3) ->
  list[str] | None` (GET `/api/tags`; `None`=unreachable, `[]`=empty; never raises).
- `revenant_cli/preflight.py`: `PreflightResult` + `check(base_url, model)`.
  Unreachable → "start Ollama with `ollama serve`"; model absent (normalize
  `:latest`) → "run `ollama pull <model>`" + available list; else ok. Wire into
  `_build_agent` after `build_config`; **hard-fail** (→ `None` → exit 2) on both;
  `--skip-preflight` in `_add_common_flags`.
- `cli._actionable(text)` wraps mid-run connect/404 errors. `OLLAMA_HOST` env +
  `_normalize_base_url` (bare `host:port` → URL).

### U2 — doctor + models + picker (Phase A)
- `revenant doctor` (fill the config-stub area): resolve config (factor
  `_resolve_endpoint`), run preflight, print reachability + pulled models +
  resolved config + mismatch flag. Exit 0/1.
- `revenant models`: list `/api/tags`, mark the resolved `code` model.
- Interactive picker when preflight finds the model absent + TTY; persist via
  `config.write_model_choice(model, scope)` (minimal `model = "..."` upsert; user
  config default, `--project` for `.revenant.toml`).
- **Ship Phase A as its own PR.**

### U3 — Console abstraction (Phase B)
`revenant_cli/console.py`: `make_console(*, color, no_color_env)` (guarded `import
rich`); `Console` interface (`event`, `status`, `session_header`, `approval`,
`diff`, `prompt`, `confirm`, `status_spinner`, `banner/panel/rule`, `error/print`);
`PlainConsole` (byte-identical to today's output — safety net) and `RichConsole`
(panels, `Syntax`, `difflib` diff, `Status`/`Live` spinner). `pyproject` `[rich]`;
`revenant.spec` guarded `collect_submodules("rich")`; installer build adds `[rich]`.

### U4 — reroute chrome + tuple migration (Phase B)
Reroute `on_event`→`console.event` (raw events), approver→`console.approval` +
real `difflib` diff, add the `status_spinner` around `loop.run`, replace the ~9
status prints with `console.session_header`, REPL prompt→`console.prompt`. Add
`NO_COLOR` to `_color_enabled`. Migrate the `_build_agent` tuple to return
`console` + update the `_patch_build_agent*` test helpers. **Ship Phase B as its
own PR; consider a 0.4.0 release.**

## Test plan (model-free / offline; CI runs bare `pytest -q`)
- U0: role→`qwen2.5-coder:7b`. U1: `list_local_models` (monkeypatched urlopen);
  preflight unreachable/missing/ok (+`:latest`); `_actionable`; OLLAMA_HOST +
  normalize. U2: `cmd_doctor`/`cmd_models` via capsys + monkeypatch;
  `write_model_choice` round-trip. U3: **byte-parity** of `PlainConsole` vs the
  old `make_printer`; no-rich→PlainConsole; `NO_COLOR` disables; rich path behind
  `pytest.importorskip("rich")`. U4: `difflib` diff; approver declines on non-yes.

## Acceptance criteria
- [ ] A by-the-docs first run works (one `ollama pull qwen2.5-coder:7b`).
- [ ] Ollama down / model missing → clear, actionable message (+ picker); mid-run
      failures get the same hints.
- [ ] `revenant doctor` / `revenant models` work and are scriptable.
- [ ] With `[rich]`: panels, real edit diffs, a "thinking…" spinner, a grouped
      session header. Without rich (or piped/CI): byte-identical to today.
- [ ] `NO_COLOR` honored; frozen binary loads rich or cleanly falls back.
- [ ] Suite green (bare `pytest`); ADR-0016 + README updated per slice.

## Progress log
- 2026-07-31 — Accepted. Strategy + U0–U4 spec written before code (durable
  record first, per the P0–P8 / H-series workflow).
- 2026-07-31 — **Phase A (setup UX) Implemented** — U0/U1/U2.
  - U0: `DEFAULT_PROFILES` `code`/`router`/`summary` → `qwen2.5-coder:7b`
    (`test_default_profiles.py` locks it). One `ollama pull` first-run works.
  - U1: `local_llm_writer.list_local_models`; `preflight.py` (hard-fail +
    actionable serve/pull messages, `:latest` normalization); `cli._actionable`
    error wrapping; `OLLAMA_HOST` + `_normalize_base_url`; `--skip-preflight`.
    Wired into `_build_agent`. `config.write_model_choice` (scalar upsert).
  - U2: `revenant doctor` (reachability + pulled models + resolved config +
    ready/mismatch) and `revenant models` (list, marks in-use); interactive model
    picker (`_offer_model_picker`) on a failed preflight (TTY + reachable),
    persisting the choice. Verified end-to-end against real Ollama.
  - Tests: 507 (was 486) — `test_default_profiles.py`, `test_preflight.py`,
    doctor/models + `write_model_choice` cases. Next: Phase B (U3/U4 console).
- 2026-08-01 — **Phase B (rich console) Implemented** — U3/U4.
  - U3: `console.py` (`make_console`, `PlainConsole`, `unified_diff`) +
    `_rich_console.py` (`RichConsole`: panel header, syntax-highlighted
    observations, real unified diff for edits, `Status` spinner that pauses on
    every print). `rich` is an **optional dep** (`revenant-cli[rich]`); guarded
    import → `PlainConsole` fallback. `revenant.spec` + installer build bundle
    `rich`. `test_console.py`: **byte-parity** of PlainConsole vs the legacy
    renderer (18 event×color cases), backend selection, NO_COLOR, rich path.
  - U4: `_build_agent` builds one `console` (via `_color_enabled` incl. NO_COLOR),
    stashes it on the loop; `on_event=_on_event(console)` (actionable errors),
    `approve=_make_approver(console)` (rich diff panel), `session_header` +
    `status_spinner` in `cmd_run`/`cmd_chat`. Kept the 5-tuple (`color` slot) and
    stashed the console — zero churn to callers/test-helpers.
  - Verified end-to-end with rich (header + live spinner + event stream + diff
    approval, all interleaving cleanly) AND without rich (byte-identical
    fallback). Tests 507 → 532 (531 + 1 rich-skip when rich absent).
  - Deviation from plan: kept `_build_agent`'s `color` tuple slot and stashed
    `loop._console` instead of replacing the slot — same single-console outcome,
    far lower risk (no changes to the 4 unpack sites, the many `color[...]`
    prints, or the test helpers). Startup status lines (`graph:`/`mcp:`/…) left as
    plain dim prints (not folded into the header) — low value, high churn.

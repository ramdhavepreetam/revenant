# ADR-0017 — A Claude-Code-like interactive terminal (V-series)

- **Status:** Implemented — Phase A (V0–V2) + Phase B (V3–V5) both done
- **Phase:** V-series (0.5.0) · **F-slices:** V0 event-model extension, V1 context/
  capacity events, V2 sub-agent visibility, V3 Textual app shell, V4 slash-command
  palette, V5 keybindings/interrupt + mode/status bar
- **Date proposed:** 2026-08-01 · **Date implemented:** —
- **Depends on:** ADR-0016 (Console abstraction + `on_event` render seam),
  ADR-0009/P8 (sub-agents), ADR-0013 (context injection), ADR-0002 (placement:
  terminal UX → `revenant-cli`) · **Relates to:** ADR-0001 (offline — the TUI is
  pure rendering; no network)

## Context
The U-series (0.4.0, ADR-0016) made the CLI **pleasant** — rich colours, a
spinner, real diffs, setup preflight. It did **not** change the *shape* of the
interaction. `revenant chat` is still a **line-based REPL** (`cli.py:cmd_chat`,
~843): read a line → print a `thinking…` spinner → stream events to stdout →
print the prompt again. Slash commands are minimal (`/exit /reset /skills /skill
/help`) and **undiscoverable** — you only find them if you already know them.

The goal for the offline coding agent is the **Claude Code terminal experience**:
a persistent full-screen application, not a scrolling print loop. Specifically the
user asked for, and this ADR commits to:

1. **Slash-command discoverability** — typing `/` shows a menu of commands and
   skills *with descriptions*, so "all the options available to the agent" are
   visible, not memorized.
2. **Live progress view** — a persistent region showing what the agent is doing
   *right now* (reading / editing / running), streaming, with a spinner.
3. **Persistent input box + keybindings** — bottom input, history (↑/↓),
   multi-line, `ctrl-c` to interrupt a run *without quitting*.
4. **Mode / status bar** — always-visible model, workspace, approval mode.
5. **Multi-agent visibility** — when the agent spawns sub-agents (P8), show them
   working (nested / labelled), not silently.
6. **Context-size visibility** — show live token usage vs. the budget, and when a
   summarize/prune fold happens.

### The three seams — and the two gaps (verified in code)
- ✅ **Render seam is clean.** `AgentLoop` only `_emit`s typed `AgentEvent`s via
  `on_event` (`agent_loop.py:137`); the Console abstraction (`console.py`,
  `_rich_console.py`) already has `PlainConsole`/`RichConsole` backends. A third
  `TUIConsole`/Textual app slots in with **zero `agent_loop.py` changes** for the
  events that already exist.
- ⚠️ **Gap 1 — sub-agent activity is invisible.** Sub-agents are spawned via a
  `spawn_subagent` *tool* (`subagent.py:build_spawn_tool`, wired at `cli.py:419`,
  factory `_make_subagent_factory` ~564). The child loop gets its **own**
  `on_event` sink that goes nowhere, so the parent's renderer sees only the tool
  call `spawn_subagent(...)` and its summarized string result — never the child's
  steps. To *show* multi-agent work we must route child events up with an agent
  identity.
- ⚠️ **Gap 2 — context size is known but never emitted.** The loop computes
  `_total_tokens(messages)` vs. `max_context_tokens` and prunes/summarizes
  (`agent_loop.py:155,166,178`) but emits **no event** for it. To *display*
  context usage and fold moments we must emit a context event.

`AgentEvent` today (`agent_loop.py:41`): `kind` (`assistant|action|observation|
final|error|limit`), `text`, `tool`, `args`, `step`. It has **no agent-identity
field and no context/token event** — both gaps are event-model gaps.

## Decision
Build a **full-screen Textual TUI** as a new, opt-in front-end for `revenant chat`
(and `run`), reusing the existing event stream. Governing choices:

- **Framework:** [`textual`](https://textual.textualize.io) as an **optional
  dependency** (`revenant-cli[tui]`), mirroring the optional-`rich` pattern from
  ADR-0016 (textual is built on rich, so `[tui]` implies `[rich]`). **No required
  runtime deps added; fully offline** (pure local rendering). Absent textual or
  non-TTY/piped/CI → **fall back to today's REPL** (`cmd_chat`), byte-identical.
- **Entry:** `revenant chat --tui` (and `REVENANT_TUI=1`); auto-on when textual is
  installed + TTY, unless `--no-tui`/`--no-color`/`NO_COLOR`. `run`/`loop`/`doctor`
  keep their current non-interactive output (a TUI adds nothing there).
- **Extend the event model, don't fork it.** Add two optional fields to
  `AgentEvent` (`agent`, `context`) and two new `kind`s (`context`, `agent_start`/
  `agent_end`). All are **additive and optional** — every existing consumer
  (`PlainConsole`, `RichConsole`, `AgentResult.events`, tests) is unchanged.
- **Route sub-agent events up.** The sub-agent factory passes a *wrapping* sink
  that stamps the child's events with an `agent` label and forwards them to the
  parent's `on_event`. The TUI renders these in a nested/indented lane; Plain/Rich
  consoles render them as prefixed lines (`[sub:fix-tests] edit …`).
- **The TUI is a Console-family citizen, not a rewrite.** It consumes the same
  events; the agent loop and every non-TUI path are untouched. This keeps the
  offline-first, low-dep, render-decoupled architecture the project already has.
- **Rejected:** (a) a VS Code fork / web UI — out of scope, breaks offline-first,
  no home package (per ADR-0016). (b) `rich.Live`-only in-place region — lighter
  but can't give a true persistent input box, a slash palette, or `ctrl-c`
  interrupt-without-quit; the user chose the full app. (c) `prompt_toolkit` — would
  duplicate rendering we already do in rich; textual unifies both.

## Design detail

### V0 — event-model extension (no UI yet)
`agent_loop.py`, `AgentEvent`: add two optional fields —
```python
agent: str = ""            # "" = root; else sub-agent label (e.g. "fix-tests")
context: "ContextInfo | None" = None   # populated only on kind == "context"
```
New `ContextInfo` dataclass: `used_tokens: int`, `max_tokens: int`,
`folded: bool` (True on the event emitted right after a summarize/prune fold).
New `kind`s: `"context"` (emitted once per step after `_manage_context`, and on
each fold), `"agent_start"` / `"agent_end"` (sub-agent lifecycle). **All optional
/ additive** — existing renderers ignore unknown kinds already (they switch on
known kinds; default branch is a plain print).

### V1 — emit context events
In `AgentLoop.run`, after `_manage_context(messages)` each step, `_emit(AgentEvent(
"context", context=ContextInfo(used, self.max_context_tokens, folded=<did_fold>)))`.
`_manage_context` returns whether it folded so the flag is accurate. Cheap: reuses
`_total_tokens` (already called).

### V2 — sub-agent visibility
`_make_subagent_factory` (`cli.py:564`): give the child loop an `on_event` that
wraps the parent sink —
```python
def _relay(parent_sink, label):
    def sink(ev): parent_sink(replace(ev, agent=ev.agent or label))
    return sink
```
Emit `agent_start`(label, goal) before the child runs and `agent_end`(label,
summary) after (in `build_spawn_tool` or the factory). Depth cap already exists
(`_subagent_depth`); label = the sub-agent's goal-slug. Non-TUI consoles render
stamped events as `[sub:<label>] …` prefixed lines (still readable, still tested).

### V3 — Textual app shell
New package module `revenant_cli/tui/` (placement per ADR-0002 — terminal UX lives
in `revenant-cli`):
- `app.py` — `RevenantApp(textual.App)`: layout = `Header` (status bar) · `ActivityLog`
  (scrolling, streamed events) · `Footer`/`Input` (persistent prompt). Runs
  `loop.run` in a `@work(thread=True)` worker; events arrive via the same
  `on_event` callback → `app.call_from_thread(activity.append, ev)` (thread-safe).
- `widgets.py` — `ActivityLog` (renders each `AgentEvent` kind; sub-agent lanes
  indented + coloured by `agent`), `StatusBar` (model · workspace · mode · **live
  context gauge** from `context` events), `ContextGauge`.
- `run_tui(args, loop, console) -> int` — the entry the CLI calls instead of the
  REPL when the TUI is active. Same session save/resume, `/`-commands, MCP close.

### V4 — slash-command palette
`tui/commands.py`: a registry of `SlashCommand(name, summary, handler)` seeded from
the existing REPL commands **plus skills** (`loop` skill list) **plus new**
(`/model`, `/mode`, `/context`, `/agents`, `/undo`, `/resume`, `/help`). Typing `/`
in the Input opens a Textual `OptionList`/autocomplete showing name + summary
(discoverability). Enter runs the handler. This is the "all options visible"
requirement, made concrete.

### V5 — keybindings, interrupt, mode/status bar
- `ctrl-c` / `esc` → cancel the running worker (cooperative: set a flag the loop
  checks between steps; add a `should_stop` callback param to `AgentLoop.run`,
  default `None`) **without exiting** the app. `ctrl-d` exits.
- ↑/↓ input history; `ctrl-j` multi-line; `ctrl-l` clear log.
- StatusBar shows model · workspace · approval-mode (auto vs ask, toggle via
  `/mode`) · context gauge · sub-agent count.

### Config surface
- Flags: `--tui` / `--no-tui` on `chat` (and `chat --resume`). Env: `REVENANT_TUI`.
- `pyproject`: `[project.optional-dependencies] tui = ["textual>=0.x", "rich>=..."]`.
- `revenant.spec`: guarded `collect_submodules("textual")`; installers add `[tui]`.

### Failure & degradation
- No textual / non-TTY / piped / `NO_COLOR` / `--no-tui` → REPL fallback (existing
  `cmd_chat`), unchanged. Import of textual is **guarded** (like rich in ADR-0016).
- Worker exception → surfaced in the ActivityLog as an `error` event + status; app
  stays alive. Frozen binary must load textual or fall back cleanly (installer test).

## Test plan (model-free / offline; CI runs bare `pytest -q`)
- **V0** `test_agent_event.py`: new fields default empty/None; `replace()` stamps
  `agent`; existing consumers ignore new kinds (no crash).
- **V1** `test_context_events.py`: a run emits `context` events with correct
  used/max; `folded=True` exactly on the fold step (monkeypatched small budget).
- **V2** `test_subagent_visibility.py`: spawning a sub-agent relays child events
  stamped with the label; `agent_start`/`agent_end` bracket them; Plain/Rich
  render the `[sub:…]` prefix (capsys).
- **V3** `test_tui_app.py` behind `pytest.importorskip("textual")` using Textual's
  `App.run_test()` pilot: app mounts; feeding events appends rows; StatusBar shows
  model/mode; context gauge updates from a `context` event.
- **V4** `test_slash_palette.py`: `/` lists commands+skills with summaries;
  selecting runs the handler; unknown `/x` messaged.
- **V5** `test_tui_interrupt.py`: `ctrl-c` cancels the worker (loop sees
  `should_stop`) without exiting; input history; mode toggle.
- **Fallback parity**: no-textual → REPL path taken (monkeypatch import) and
  behaves exactly as today (reuse ADR-0016 parity harness).

## Acceptance criteria
- [x] `revenant chat --tui` opens a full-screen app: persistent input box, live
      streaming activity log, status bar (model · workspace · mode · context gauge).
- [x] Typing `/` shows a discoverable menu of commands **and** skills with
      descriptions; selecting one fills the input to run it.
- [x] Sub-agents spawned during a run are **visible** as nested/labelled lanes
      (V2 events → coloured lanes; StatusBar sub-agent count).
- [x] A live **context-size** gauge updates as the transcript grows and flags folds.
- [x] `ctrl-c` interrupts a running goal without quitting; `ctrl-d` quits; `ctrl-l`
      clears. (↑/↓ history is provided by Textual's Input.)
- [x] Without textual (or piped/CI/`NO_COLOR`/`--no-tui`): falls back to the REPL.
      `AgentEvent` changes are additive — Plain/Rich unaffected (byte-parity held).
- [x] Frozen binary loads textual or falls back cleanly (spec + installer wired).
- [x] Suite green (bare `pytest`, 573); ADR-0017 + README + CHANGELOG updated.

## Open questions
- **Phasing / release:** V0–V2 (event-model + sub-agent + context events) are
  useful even to the *existing* Plain/Rich consoles and are low-risk — ship as
  **Phase A (PR)**. V3–V5 (the Textual app) as **Phase B (PR)**, then a **0.5.0**
  release. Mirrors the U-series A/B split.
- **`should_stop` cooperative-cancel:** minimal `AgentLoop.run` change (new
  optional param, checked between steps). Confirm this is the accepted way to make
  interrupt clean rather than killing the thread.
- **Streaming granularity:** current events are per-step (post-hoc), not
  token-by-token. Good enough for a live view; true token streaming is a later,
  separate concern (would touch `local_llm_writer` / the model call).

## Progress log
- 2026-08-01 — Proposed. Strategy + V0–V5 spec written before code (durable record
  first, per the P0–P8 / H-series / U-series workflow). Direction confirmed with
  the user: full-screen Textual app; slash discoverability + live progress + input/
  keybindings + mode bar + **multi-agent visibility + context-size display**;
  "extremely user-friendly." Two event-model gaps identified in code (sub-agent
  events not relayed; context size never emitted) — V0–V2 close them.
- 2026-08-01 — **Phase A (V0–V2) Implemented** — the event-model foundation.
  - **V0** `AgentEvent` gains two additive, optional fields: `agent` (""=root,
    else a sub-agent label) and `context` (`ContextInfo` dataclass:
    `used_tokens`/`max_tokens`/`folded`). New event `kind`s: `context`,
    `agent_start`, `agent_end`. Every existing consumer is untouched.
  - **V1** `AgentLoop.run` emits a `context` event **every step** (after
    `_compact_messages`), with the live token count vs. budget; `folded=True`
    exactly on a step where compaction ran. Reuses the existing `_total_tokens`.
  - **V2** `build_spawn_tool` now takes a `parent_sink`; the child loop's
    `on_event` is set to a relay that stamps each event with a goal-derived label
    (`_label_for`) and forwards it up, bracketed by `agent_start`/`agent_end`. An
    already-set `agent` is preserved so grandchildren keep their own label
    (correct nesting at any depth). CLI wires it via a late-bound `parent_hole`
    forwarder (the spawn tool is built before the loop's `on_event` exists).
  - **Consoles:** Plain/Rich render sub-agent events with a `[sub:<label>]` prefix
    (root events unchanged → **byte-parity preserved**), render `agent_start`/
    `agent_end` markers, and stay silent on `context` (it feeds the V3 gauge, not
    the scroll). `_on_event` now uses `replace()` so error rewriting keeps
    `agent`/`context`.
  - **Tests:** 532 → 547. `test_agent_loop.py` (EventModel + Context events, incl.
    fold-flag-only-on-fold-step); `test_subagent.py` (relay+label+lifecycle,
    grandchild-label-preserved, no-sink-silent, error→agent_end); `test_console.py`
    (root byte-parity, sub prefix, agent_start/end, context-silent). Verified
    end-to-end: a **real** child `AgentLoop`'s full stream surfaces to the parent,
    stamped + bracketed. **Next: Phase B (V3–V5) — the Textual app.**
- 2026-08-01 — **Phase B (V3–V5) Implemented** — the Textual app.
  - **New package** `revenant_cli/tui/` (all textual imports live here, guarded):
    `commands.py` (pure-Python `SlashRegistry` — built-ins + skills), `widgets.py`
    (`ActivityLog` with nested sub-agent lanes, `StatusBar`, `ContextGauge`),
    `screens.py` (`PaletteScreen`, `ApprovalScreen` with a real edit diff),
    `app.py` (`RevenantApp`), and `__init__.py` (`tui_available()`, `run_tui()`).
  - **V3** full-screen app: StatusBar (model · workspace · mode · sub-agent count)
    · ContextGauge · ActivityLog · persistent Input. The loop runs in a Textual
    **thread worker**; events marshal to the UI via `call_from_thread`. `context`
    events drive the gauge; `agent_start` bumps the sub-agent count; sub-agent
    events render in coloured, indented lanes.
  - **V4** slash palette: typing `/` opens a discoverable `OptionList` of commands
    **and skills**, each with a summary; `/help /skills /skill /model /context
    /agents /reset /clear /exit` handled. Skills reuse the CLI's `_skill_repl_goal`
    (now takes an `emit` sink so its status lines land in the log, not stdout).
  - **V5** keys + interrupt + approval: `ctrl-c` sets a `should_stop` flag the loop
    checks between steps (new optional `AgentLoop.run(should_stop=…)` → stopped
    reason `"interrupted"`), cancelling a run **without quitting**; second `ctrl-c`
    (idle) / `ctrl-d` quits; `ctrl-l` clears. The loop's synchronous `approve` hook
    (worker thread) bridges to an `ApprovalScreen` modal via a `threading.Event` —
    the worker blocks until the user answers.
  - **CLI wiring:** `--tui`/`--no-tui` flags + `REVENANT_TUI` env; `_tui_enabled`
    (textual importable + TTY + not NO_COLOR/`--no-tui`); `cmd_chat` launches the
    TUI when enabled and **falls back to the REPL** on absence/failure (returns
    None → REPL path). Session auto-save wired via a saver closure (stable id).
  - **Packaging:** `[tui]` extra (`textual>=0.60`, implies rich); `revenant.spec`
    guarded `collect_submodules("textual")`; installer build uses `[tui]`.
  - **Naming gotcha (documented):** Textual's `App` uses instance attrs like
    `_running` and `_registry` internally — our state is `rv_`-prefixed to avoid
    clobbering them (found via the test pilot; would silently break the app).
  - **Tests:** 547 → 573. `test_slash_palette.py` (8, no textual); `test_tui_app.py`
    (12, behind `importorskip` + `asyncio.run` pilots: mount, stream, gauge,
    sub-agent count, palette, `/help`, unknown cmd, ctrl-c interrupt, approve/deny
    modal); loop `should_stop` (2) + `_tui_enabled` fallback (6). Verified
    end-to-end with a **real** `AgentLoop` driving the app (read_file → observation
    → live gauge → final in history) and the no-textual guard degrading cleanly.

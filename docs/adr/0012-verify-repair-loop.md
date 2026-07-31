# ADR-0012 — Verify → repair loop (H1)

- **Status:** Proposed
- **Phase:** H1 (0.3.0 lead) · **F-slices:** H1.1 verifier, H1.2 after_tool hook, H1.3 repair budget, H1.4 targeted verification
- **Date proposed:** 2026-07-31 · **Date implemented:** —
- **Depends on:** ADR-0011 (strategy), ADR-0003 (`before_tool` seam), ADR-0006 (loop-driver), ADR-0010 (undo), ADR-0008 (code graph)
- **Blocks:** ADR-0014 (H3 per-step verify)

## Context
The single highest-leverage weakness (ADR-0011, failure ①): after the model
edits a file, **nothing checks it.** A 14B's dominant failure is not broken
syntax but *code that compiles and looks right yet is wrong* — an unchecked edit
ships exactly that. Turning "the model wrote plausible-but-broken code" into "the
harness caught it and repaired it" is the biggest reliability win available, and
it is mostly wiring together seams we already shipped.

## Decision
Treat every edit as a **proposal**. After a mutating tool runs, a deterministic
**verifier** checks the result; on failure the harness does **not** surface it to
the user — it feeds the exact error back as the next observation and lets the
loop **repair**, bounded by a budget, with **undo** as the revert net when repair
is exhausted.

Rejected: verifying only at the end of a run (too late — errors compound); making
verification a tool the model must call (defeats the point — a weak model won't).

## Design detail

### H1.1 — Verifier abstraction (`nerva_agent/verify.py`)
```python
@dataclass
class VerifyResult:
    ok: bool
    errors: str = ""        # exact compiler/test/lint output, fed back verbatim
    checker: str = ""       # which check produced this

class Verifier(Protocol):
    def check(self, changed_paths: list[str]) -> VerifyResult: ...
```
Built-in verifiers, composed in order (fail-fast):
- `PyCompileVerifier` — `py_compile` each changed `.py` (stdlib; catches syntax).
- `CommandVerifier(cmd)` — run a configured command (typecheck / lint / tests);
  `ok = exit 0`, `errors = captured stderr/stdout tail`.
- A `CompositeVerifier` runs a list and stops at the first failure.

Config in `.revenant.toml`:
```toml
[verify]
enabled = true
commands = ["ruff check {paths}", "pytest -q {tests}"]   # {paths}/{tests} substituted
max_repair_attempts = 3
```

### H1.2 — `after_tool` hook (`agent_loop.py`)
Symmetric to the existing `before_tool` hook (undo). Signature:
`after_tool(tool_name, args, observation) -> str | None` — returns extra text to
**append to the observation** the model sees, or None. It fires right after
`registry.dispatch` (today line 333), only for mutating tools. Like `before_tool`,
a hook error never crashes the loop.

The CLI wires `after_tool` to run the verifier on the changed paths (derived from
`args["path"]` for edit/write; for `run_bash`, the whole verifier set) and, on
failure, append: `"VERIFICATION FAILED (<checker>):\n<errors>\nFix this before
continuing."` — so the model's very next turn is a repair with the precise error
in hand.

### H1.3 — Repair budget + revert (driver in `revenant-cli`)
- `max_repair_attempts` (config/flag) caps consecutive failed-verify → repair
  cycles **per edit boundary**.
- On exhaustion: revert that edit via the checkpointer (P2.5/P8 undo), emit a
  clear "couldn't get this green after N attempts" observation, and hand control
  back (in `run`/`chat`) or stop the iteration (in `loop`). **Never silently ship
  a failing edit.**
- Reuses the loop-driver's iterate-until-predicate machinery (ADR-0006): the
  predicate here is "verifier passes".

### H1.4 — Targeted verification (uses the code graph, ADR-0008)
Running the whole suite after every edit is too slow to do every time. Use the
graph to **select only the tests that touch the changed symbol** (its callers /
importing files), so the check is cheap enough to run on each edit. Falls back to
the full configured command when the graph can't narrow it.

## Failure & degradation
- No verifier configured / `enabled = false` → behave exactly as today (no-op).
- A verifier command that itself errors (missing tool) → warn once, skip that
  checker, continue with the rest — never block the edit on a broken checker.
- Non-git, no-undo workspace → repair still works; revert falls back to
  file-snapshots (ADR-0010).

## Test plan
`tests/test_verify.py`:
- [ ] `PyCompileVerifier` flags a syntax error, passes clean code.
- [ ] `CommandVerifier` maps exit 0 → ok, non-zero → errors (captured tail).
- [ ] `CompositeVerifier` fails fast at the first failing checker.
- [ ] `{paths}` / `{tests}` substitution; missing checker tool degrades.
`tests/test_agent_loop.py` (after_tool):
- [ ] `after_tool` fires after a mutating tool and its return is appended to the
      observation; not called for read-only tools; a hook error doesn't crash.
`tests/test_cli.py` (repair):
- [ ] a failing verify triggers a repair turn with the error text in-context.
- [ ] budget exhaustion reverts the edit and stops with a clear message.

## Acceptance criteria
- [ ] With `[verify]` configured, an edit that breaks compilation/tests is caught
      and the model gets the exact error back to repair — verified end-to-end.
- [ ] Repair is bounded; exhaustion reverts via undo and never ships broken code.
- [ ] Verification is off by default-safe (no config → no behavior change).
- [ ] Offline; tests green; ADR-0011/0012 + README updated.

## Progress log
- 2026-07-31 — Proposed. Seam confirmed at `agent_loop.py` dispatch (mirrors
  `before_tool`); reuses loop-driver + undo + code-graph test selection.

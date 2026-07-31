# ADR-0005 — Skills: reusable packaged workflows (Phase 4)

- **Status:** Implemented
- **Phase:** P4 · **F-slices:** F12.1 format/loader, F12.2 progressive disclosure, F12.3 skill-scoped tools, F12.4 CLI+/skill
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
- **Depends on:** ADR-0003 (registry, preamble), project_context patterns · **Blocks:** ADR-0009 (sub-agent skill scoping)
- **Relates to:** ADR-0004 (skill-declared MCP tools)

## Context
Users repeat the same multi-step workflows: "run the test suite and fix
failures", "write a DB migration", "review this diff". Today each is re-prompted
from scratch. **Skills** are named, versioned instruction+tool bundles the agent
loads on demand — the offline analog of Claude Code skills. This mirrors what
`project_context` already does (find a doc, fold it into the preamble), but
generalizes it from one implicit doc to many explicit, invokable ones.

## Decision
A **file-based skill format** (`SKILL.md` + frontmatter) discovered from
`.revenant/skills/`, loaded with **progressive disclosure**: only each skill's
one-line description sits in-context; its full body loads when invoked. A skill
may declare a tool subset (including MCP tools from ADR-0004) activated only
while it runs.

Rejected: encoding skills as Python plugins — that breaks the "edit a markdown
file" simplicity and complicates the offline/no-build story.

## Design detail

### F12.1 — Format & loader (`nerva_agent/skills.py`)
Skill directory: `.revenant/skills/<name>/SKILL.md` (project) and
`~/.config/revenant/skills/<name>/SKILL.md` (user).

**Frontmatter is TOML fenced by `+++`** (decision 2026-07-30). Rationale: the
repo forbids new runtime deps (ADR-0001/0002) and already uses stdlib `tomllib`
for config; TOML frontmatter reuses that robust parser with zero new
dependencies. The trade-off vs. the common `---` YAML convention is accepted —
we control the format and the parser stays trivial and correct.
```markdown
+++
name = "run-tests"
description = "Run the test suite and summarize failures."   # the only thing in-context until invoked
trigger = "/run-tests"                    # optional explicit slash trigger
tools = ["run_bash", "read_file", "edit_file"]   # optional scoped tool set
+++
<the full instructions / procedure body>
```
- `Skill{name, description, trigger, tools, body, path, source}` where
  `source` ∈ {"project", "user"}.
- `discover_skills(project_dir, user_dir) -> list[Skill]` — walk both roots,
  parse frontmatter, tolerate malformed files (skip + warn, like `config.py`).
  Project skills override user skills of the same name. Takes explicit roots so
  `nerva_agent` stays free of CLI path conventions (the CLI passes
  `~/.config/revenant/skills` and `<workspace>/.revenant/skills`).

### F12.2 — Progressive disclosure (`agent_loop` preamble + a skill index)
- The system preamble gains a compact **skill index**: `name — description` lines
  only. Cheap on tokens (bounded by count, not body size).
- When a skill is invoked (slash command, or the model emits an
  `invoke_skill(name)` tool), its **body** is injected as a system/user message
  and the loop continues with it in context.
- On completion or `/reset`, the body is dropped from the active window so it
  doesn't bloat the compaction budget. The index stays.

### F12.3 — Skill-scoped tool sets (`ToolRegistry`)
- A skill's `tools:` list names which registry tools it needs.
- Implement as an **active-tool filter** on the registry for the duration of the
  skill (a view, not a rebuild), so MCP + built-in tools compose. If a named
  tool is absent, warn and proceed with what's available.
- Depends on P3 for MCP tool names to be resolvable.

### F12.4 — CLI & REPL (`cli.py`, `cmd_chat`)
- `revenant skills list|add|show <name>`.
- REPL slash-command `/skill <name>` (and any skill's own `trigger`), mirroring
  the existing `/reset`·`/help` handling in `cmd_chat`.
- One-shot: `revenant run --skill run-tests "…"`.

## Failure & degradation
- Malformed `SKILL.md` → skipped with a warning; never crashes discovery.
- Unknown skill name → clear error, list available skills.
- Missing declared tool → warn, run with available tools.

## Test plan — DONE (25 tests, 2026-07-30)
`tests/test_skills.py` (18):
- [x] `+++` TOML frontmatter parsed; no-fence / unclosed-fence → body-only;
      malformed TOML raises (caught by loader).
- [x] discover parses frontmatter; malformed file skipped with warning.
- [x] project skills override user skills of the same name; name defaults to dir.
- [x] discovery sorted by name; missing roots → empty.
- [x] skill index rendering contains only name+description (not bodies).
- [x] `scope_registry` limits to `tools:`, composes built-in + MCP names, empty
      list → full registry, missing tool warns and continues.
- [x] `compose_skill_body` injects body under an "Active skill" header.
`tests/test_cli.py` (7):
- [x] `revenant skills list` / `show` render discovered skills; unknown errors;
      none-found message; parser flag-ordering (both sides of the sub-action).
- [x] `/skill <name>` in the REPL loads the body into the preamble, scopes the
      registry to the skill's tools, and runs the body as the turn goal.
- [x] `/skill <unknown>` prints and runs no turn.

## Acceptance criteria
- [x] A `run-tests` skill is discoverable, shows its body, and (via `/skill`) is
      loaded as the active procedure with a scoped tool set.
- [x] Only descriptions (not bodies) are in-context until invocation (the index
      renders name+description; the body loads on `/skill`).
- [x] Skill-scoped tools compose with MCP tools (scope_registry filters over the
      full registry that already contains built-in + MCP tools).
- [x] Tests green (247 → 272); ADR + README updated; F12 marked Implemented.

## Implementation notes (what actually shipped)
- **Frontmatter is `+++` TOML** (see the decision box above): stdlib `tomllib`,
  zero new deps.
- **Discovery API** takes explicit roots — `discover_skills(project_dir,
  user_dir)` — so `nerva_agent` stays free of CLI path conventions; the CLI's
  `_skill_dirs` supplies `<ws>/.revenant/skills` and `~/.config/revenant/skills`.
- **Progressive disclosure:** the index (names+descriptions) is folded into the
  system preamble in `_build_agent`; the body loads only on `/skill` via
  `compose_skill_body`, and the registry is scoped via `scope_registry`. Both
  compose with the P3 MCP tools already in the registry.
- **Deviation:** `revenant run --skill <name>` (one-shot) was **deferred** —
  it needs the `run` positional `goal` to become optional. `skills list/show`
  and the REPL `/skill` cover invocation for now (parallels the `mcp add`
  deferral in ADR-0004).

## Progress log
- 2026-07-30 — Proposed. Generalizes the existing `project_context` doc-folding.
- 2026-07-30 — **Implemented.** F12.1–F12.4 built and tested (25 new tests,
  suite 247 → 272). Verified `skills list/show` end-to-end via the real CLI.
  Frontmatter decided as `+++` TOML. `run --skill` deferred. Status →
  Implemented. Next phase: P6 Resume (ADR-0007) or P5 Loops (ADR-0006).

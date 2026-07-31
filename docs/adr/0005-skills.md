# ADR-0005 — Skills: reusable packaged workflows (Phase 4)

- **Status:** Proposed
- **Phase:** P4 · **F-slices:** F12.1 format/loader, F12.2 progressive disclosure, F12.3 skill-scoped tools, F12.4 CLI+/skill
- **Date proposed:** 2026-07-30 · **Date implemented:** —
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
`~/.config/revenant/skills/<name>/SKILL.md` (user). Frontmatter:
```markdown
---
name: run-tests
description: Run the test suite and summarize failures.   # the only thing in-context until invoked
trigger: /run-tests            # optional explicit slash trigger
tools: [run_bash, read_file, edit_file]   # optional scoped tool set
---
<the full instructions / procedure body>
```
- `Skill{name, description, trigger, tools, body, path, source}`.
- `discover_skills(workspace) -> list[Skill]` — walk both dirs (respecting
  ignore globs), parse frontmatter, tolerate malformed files (skip + warn, like
  `config.py`).

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

## Test plan
`tests/test_skills.py`:
- [ ] discover parses frontmatter; malformed file skipped with warning.
- [ ] project skills override user skills of the same name.
- [ ] skill index rendering contains only name+description (not bodies).
- [ ] invoking a skill injects its body; `tools:` filter limits the active set.
`tests/test_cli.py`:
- [ ] `/skill <name>` in the REPL loads the skill; unknown name errors cleanly.
- [ ] `revenant skills list` shows discovered skills from both sources.

## Acceptance criteria
- [ ] A `run-tests` skill runs the suite and summarizes failures when invoked.
- [ ] Only descriptions (not bodies) are in-context until invocation.
- [ ] Skill-scoped tools compose with MCP tools (post-P3).
- [ ] Tests green; ADR + README updated; F12 marked Implemented.

## Progress log
- 2026-07-30 — Proposed. Generalizes the existing `project_context` doc-folding.

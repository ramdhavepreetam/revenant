"""Skills: reusable, packaged workflows loaded on demand (F12.1, ADR-0005).

A skill is a named instruction+tool bundle the agent can invoke by name:
"run-tests", "write-migration", "review-diff". It generalizes the project-doc
grounding in `project_context` from one implicit doc to many explicit, invokable
ones, with progressive disclosure so only a one-line description sits in context
until the skill is actually used.

Format — a `SKILL.md` file with **TOML frontmatter fenced by `+++`** (chosen so
we reuse stdlib `tomllib` and add no runtime dependency; see ADR-0005):

    +++
    name = "run-tests"
    description = "Run the test suite and summarize failures."
    trigger = "/run-tests"                      # optional slash trigger
    tools = ["run_bash", "read_file"]           # optional scoped tool set
    +++
    <the instruction body the agent follows when the skill is invoked>

Discovery walks two roots — a project dir (`<ws>/.revenant/skills`) and a user
dir (`~/.config/revenant/skills`) — passed in explicitly so this engine module
stays free of CLI path conventions. A malformed skill is skipped with a warning,
never crashing discovery (mirrors `config.py`).
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from nerva_agent.agent_tools import ToolRegistry

SKILL_FILENAME = "SKILL.md"
_FENCE = "+++"


@dataclass
class Skill:
    """One discovered skill. `body` is the instruction text after the frontmatter."""

    name: str
    description: str
    body: str
    trigger: str | None = None
    tools: list[str] = field(default_factory=list)
    path: Path | None = None
    source: str = ""  # "project" | "user"

    @property
    def slash(self) -> str:
        """The invocation token: an explicit trigger, else `/<name>`."""
        return self.trigger or f"/{self.name}"


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, body).

    The frontmatter is the TOML block between the first pair of `+++` fences.
    Returns ({}, whole-text) when there's no valid frontmatter block, so a plain
    markdown file degrades to a body-only skill rather than an error. Raises
    tomllib.TOMLDecodeError only for genuinely malformed TOML inside the fences,
    which the caller catches and reports.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return {}, text.strip()
    # Find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            fm_text = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:]).strip()
            meta = tomllib.loads(fm_text) if fm_text.strip() else {}
            return meta, body
    # No closing fence: treat the whole thing as body.
    return {}, text.strip()


def _load_one(skill_md: Path, source: str) -> Skill | None:
    """Parse a single SKILL.md into a Skill, or None if unusable (with a warning)."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"warning: cannot read skill {skill_md}: {exc}", file=sys.stderr)
        return None
    try:
        meta, body = parse_skill_md(text)
    except tomllib.TOMLDecodeError as exc:
        print(f"warning: skipping skill with malformed frontmatter {skill_md}: {exc}",
              file=sys.stderr)
        return None

    # Name defaults to the containing directory so `<dir>/SKILL.md` need not repeat it.
    name = meta.get("name") or skill_md.parent.name
    if not isinstance(name, str) or not name:
        print(f"warning: skipping skill without a usable name: {skill_md}", file=sys.stderr)
        return None
    description = meta.get("description", "")
    if not isinstance(description, str):
        description = ""
    trigger = meta.get("trigger")
    trigger = trigger if isinstance(trigger, str) and trigger else None
    raw_tools = meta.get("tools") or []
    tools = [t for t in raw_tools if isinstance(t, str)] if isinstance(raw_tools, list) else []

    return Skill(
        name=name, description=description.strip(), body=body,
        trigger=trigger, tools=tools, path=skill_md, source=source,
    )


def _discover_root(root: Path, source: str) -> list[Skill]:
    """All skills under one root: `<root>/<name>/SKILL.md` (one level of dirs)."""
    if not root.is_dir():
        return []
    found: list[Skill] = []
    for child in sorted(root.iterdir()):
        skill_md = child / SKILL_FILENAME
        if child.is_dir() and skill_md.is_file():
            skill = _load_one(skill_md, source)
            if skill is not None:
                found.append(skill)
    return found


def discover_skills(
    project_dir: Path | None,
    user_dir: Path | None = None,
) -> list[Skill]:
    """Discover skills from the project and user roots.

    Project skills override user skills of the same name (project is more
    specific, mirroring config precedence). Order of the returned list is
    stable: sorted by name. Either root may be None or absent.
    """
    by_name: dict[str, Skill] = {}
    # user first, then project → project wins on name collision.
    for root, source in ((user_dir, "user"), (project_dir, "project")):
        if root is None:
            continue
        for skill in _discover_root(Path(root), source):
            by_name[skill.name] = skill
    return sorted(by_name.values(), key=lambda s: s.name)


def scope_registry(full: ToolRegistry, skill: Skill) -> ToolRegistry:
    """Return a registry limited to `skill.tools`, composed from `full` (F12.3).

    A view over the full registry (which already contains built-in + MCP tools),
    so scoping composes across sources. When `skill.tools` is empty the skill
    gets the full registry unchanged (no restriction requested). A named tool
    that isn't present is skipped with a warning — the skill runs with whatever
    of its declared tools exist, rather than failing.
    """
    if not skill.tools:
        return full
    selected = []
    for name in skill.tools:
        tool = full.get(name)
        if tool is None:
            print(f"warning: skill {skill.name!r} wants tool {name!r}, which is "
                  f"not available; continuing without it", file=sys.stderr)
            continue
        selected.append(tool)
    return ToolRegistry(selected)


def compose_skill_body(base_preamble: str, skill: Skill) -> str:
    """Inject a skill's instruction body into the preamble on invocation (F12.2).

    The body is added under a clear header so the model treats it as the active
    procedure. Dropping it later is just running with `base_preamble` again — the
    caller keeps the base and re-derives, so nothing needs "un-injecting".
    """
    return (
        f"{base_preamble}\n\n"
        f"--- Active skill: {skill.name} (follow this procedure) ---\n"
        f"{skill.body}"
    )


def render_skill_index(skills: list[Skill]) -> str:
    """A compact catalogue for the system preamble (F12.2 progressive disclosure).

    One `name — description` line per skill; bodies are deliberately omitted so
    the index cost is bounded by skill *count*, not content size. Returns '' when
    there are no skills (callers append nothing).
    """
    if not skills:
        return ""
    lines = [f"- {s.slash}: {s.description}".rstrip() for s in skills]
    return (
        "Available skills (invoke by name to load the full procedure):\n"
        + "\n".join(lines)
    )

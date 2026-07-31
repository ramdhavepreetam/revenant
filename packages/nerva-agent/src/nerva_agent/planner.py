"""Deterministic decomposition of a goal into small verified steps (H3.1, ADR-0014).

A local model (ADR-0011, failure ③) cannot hold a long plan in its head — it
loses the thread over a multi-step task. The harness answer is to stop asking it
to: **decompose the goal into small, independently-checkable steps**, and drive
them one at a time (H3.2) so the model only ever reasons about one small thing.

The plan is *model-produced but harness-owned*: we ask the model for a checklist,
parse it deterministically, and advance it ourselves. A plan we can't parse
degrades to a single step = the whole goal, so this is never worse than today.

This module is pure (no loop, no model call of its own): `parse_plan` turns model
text into a `Plan`; the CLI driver (H3.2) is what runs each step. The planning
*prompt* lives here so both the CLI and tests share one definition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Cap steps so a runaway plan can't spawn unbounded sub-agents.
MAX_STEPS = 12


@dataclass
class Step:
    """One small, independently-verifiable sub-goal."""

    index: int          # 1-based position in the plan
    goal: str           # the one-line instruction for this step


@dataclass
class Plan:
    """An ordered checklist of steps. `single` marks the degraded whole-goal case."""

    steps: list[Step] = field(default_factory=list)
    single: bool = False

    def __len__(self) -> int:
        return len(self.steps)


# The instruction we send the model to produce a plan. Kept strict and simple so
# a weak model's output is easy to parse: a numbered list, one step per line.
PLANNING_PROMPT = (
    "Break the goal below into a short, ordered checklist of small, concrete "
    "steps. Each step must be independently checkable (something you can verify "
    "passed before moving on). Output ONLY a numbered list, one step per line, "
    "no prose before or after. Keep it to the fewest steps that get the job done.\n\n"
    "Goal: {goal}"
)


# Matches "1. do a thing" / "2) do a thing" / "- do a thing" list items.
_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


def parse_plan(text: str, goal: str) -> Plan:
    """Parse a model's checklist into a Plan.

    Extracts numbered / bulleted list items in order. If nothing parses (the
    model ignored the format, or returned prose), degrade to a single-step plan
    whose one step is the original goal — the run then behaves like a normal
    single-goal run, still H1-verified.
    """
    items: list[str] = []
    for line in (text or "").splitlines():
        m = _ITEM_RE.match(line)
        if m:
            step_text = m.group(1).strip()
            if step_text:
                items.append(step_text)

    if not items:
        return Plan(steps=[Step(1, goal.strip() or "(no goal)")], single=True)

    items = items[:MAX_STEPS]
    return Plan(steps=[Step(i + 1, t) for i, t in enumerate(items)], single=False)


def render_plan(plan: Plan) -> str:
    """A compact, human/model-readable view of the plan (for progress + context)."""
    if plan.single:
        return "Plan: (single step) " + plan.steps[0].goal
    return "Plan:\n" + "\n".join(f"  {s.index}. {s.goal}" for s in plan.steps)

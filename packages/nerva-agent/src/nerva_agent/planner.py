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


# --- Adaptive planning (P0, ADR-0023) ----------------------------------------
# The harness recovers when a step stumbles: retry once with the failure fed back,
# then re-plan the remaining work rather than aborting the whole run.

# Prepended to a failed step's goal on the retry attempt.
RETRY_NUDGE = (
    "Your previous attempt at this step stopped without finishing "
    "({reason}). Try again, focusing ONLY on completing this one step:\n{goal}"
)

# Asks the model for a fresh checklist covering just the REMAINING work, given
# what's already done and why the current approach stalled.
REPLAN_PROMPT = (
    "You are revising a plan mid-way. The overall goal is:\n{goal}\n\n"
    "Already completed:\n{done}\n\n"
    "The next step did not work out: {failure}\n\n"
    "Produce a fresh, ordered checklist of the remaining steps to finish the "
    "goal, taking the failure into account. Output ONLY a numbered list, one step "
    "per line, no prose. Keep it to the fewest steps that finish the job."
)


def retry_goal(goal: str, reason: str) -> str:
    """The goal text for a retry attempt — the original step plus a nudge that
    names why the last attempt didn't finish."""
    return RETRY_NUDGE.format(goal=goal, reason=reason or "it did not reach a final answer")


def build_replan_prompt(goal: str, done: "list[str]", failure: str) -> str:
    """Render the re-plan instruction from the goal, completed step goals, and the
    failure. `done` is a list of completed step descriptions."""
    done_block = "\n".join(f"  - {d}" for d in done) or "  (nothing yet)"
    return REPLAN_PROMPT.format(goal=goal, done=done_block,
                                failure=failure or "the step stalled")


def replan(text: str, goal: str) -> Plan:
    """Parse a re-plan model reply into a Plan of the REMAINING steps.

    Reuses `parse_plan`. If the reply is unparseable, returns an empty plan
    (len 0) so the caller can decide to keep the existing remaining steps rather
    than losing the plan — the degrade is 'no change', never 'abort'.
    """
    plan = parse_plan(text, goal)
    if plan.single:
        # A single-step degrade here means "couldn't parse a real checklist";
        # signal that with an empty plan so the driver keeps its current steps.
        return Plan(steps=[], single=False)
    return plan

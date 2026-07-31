"""Tests for deterministic plan decomposition (H3.1, ADR-0014)."""
from __future__ import annotations

import pytest

from nerva_agent.planner import parse_plan, render_plan, Plan, Step, MAX_STEPS


def test_parses_numbered_list():
    text = "1. Add the function\n2. Wire it up\n3. Add a test"
    plan = parse_plan(text, "build the feature")
    assert not plan.single
    assert [s.goal for s in plan.steps] == ["Add the function", "Wire it up", "Add a test"]
    assert [s.index for s in plan.steps] == [1, 2, 3]


def test_parses_paren_and_bullet_styles():
    assert [s.goal for s in parse_plan("1) a\n2) b", "g").steps] == ["a", "b"]
    assert [s.goal for s in parse_plan("- a\n- b", "g").steps] == ["a", "b"]
    assert [s.goal for s in parse_plan("* a\n* b", "g").steps] == ["a", "b"]


def test_ignores_prose_around_the_list():
    text = "Here is my plan:\n1. First\n2. Second\nThat should do it."
    plan = parse_plan(text, "g")
    assert [s.goal for s in plan.steps] == ["First", "Second"]


def test_empty_or_prose_only_degrades_to_single_step():
    plan = parse_plan("I'll just do the whole thing directly.", "make it work")
    assert plan.single is True
    assert len(plan) == 1
    assert plan.steps[0].goal == "make it work"


def test_blank_text_degrades_to_single_step():
    plan = parse_plan("", "the goal")
    assert plan.single is True
    assert plan.steps[0].goal == "the goal"


def test_step_count_is_capped():
    text = "\n".join(f"{i}. step {i}" for i in range(1, 30))
    plan = parse_plan(text, "g")
    assert len(plan) == MAX_STEPS


def test_render_multi_step():
    plan = parse_plan("1. alpha\n2. beta", "g")
    out = render_plan(plan)
    assert "1. alpha" in out and "2. beta" in out


def test_render_single_step():
    plan = parse_plan("no list here", "do the thing")
    assert "single step" in render_plan(plan)
    assert "do the thing" in render_plan(plan)

"""Guards on the default model profiles (U0, ADR-0016).

The default `code`/`router`/`summary` roles must resolve to `qwen2.5-coder:7b` so
a first-run setup needs exactly one `ollama pull qwen2.5-coder:7b` and matches the
quickstart docs. This test locks that in — it caught (and now prevents re-drift
of) the bug where `code` resolved to a 14b model the docs never told users to pull.
"""
from __future__ import annotations

from nerva_core.aibot_profiles import DEFAULT_PROFILES
from nerva_agent.agent_router import _resolve_model_data


def _model_for(role: str) -> str:
    data = _resolve_model_data(role, DEFAULT_PROFILES)
    assert data is not None, f"role {role!r} does not resolve to a model profile"
    return data["model"]


def test_first_run_roles_use_coder_7b():
    # One pull works: the roles a fresh user hits all point at qwen2.5-coder:7b.
    for role in ("code", "router", "summary"):
        assert _model_for(role) == "qwen2.5-coder:7b", role


def test_default_code_model_matches_docs():
    # The single most important first-run guarantee (was the bug).
    assert _model_for("code") == "qwen2.5-coder:7b"


def test_power_user_profiles_still_present():
    # The heavier models remain available for users who repoint their roles.
    models = {m["model"] for m in DEFAULT_PROFILES["models"].values()}
    assert "huihui_ai/qwen2.5-coder-abliterate:14b" in models
    assert "qwen2.5:14b" in models


def test_profiles_are_internally_consistent():
    # Every role names a profile that exists in the models table.
    model_names = set(DEFAULT_PROFILES["models"])
    for role, profile in DEFAULT_PROFILES["model_roles"].items():
        if role == "fallback":
            continue  # fallback names another ROLE, not a model profile
        assert profile in model_names, f"role {role!r} → unknown profile {profile!r}"

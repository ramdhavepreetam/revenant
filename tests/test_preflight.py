"""Tests for pre-flight + model-list + actionable errors (U1, ADR-0016).

All model-free: `list_local_models` is exercised by monkeypatching urlopen;
`preflight.check` by monkeypatching `list_local_models`. No network.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from nerva_core import local_llm_writer as llm
from revenant_cli import preflight, cli


# --- list_local_models -------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_list_local_models_parses_names(monkeypatch):
    payload = {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "gemma:latest"}]}
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _FakeResp(payload))
    assert llm.list_local_models("http://x") == ["gemma:latest", "qwen2.5-coder:7b"]


def test_list_local_models_unreachable_returns_none(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    assert llm.list_local_models("http://x") is None


def test_list_local_models_empty(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _FakeResp({"models": []}))
    assert llm.list_local_models("http://x") == []


# --- preflight.check ---------------------------------------------------------

def test_preflight_unreachable(monkeypatch):
    monkeypatch.setattr(preflight, "list_local_models", lambda *a, **k: None)
    r = preflight.check("http://x", "qwen2.5-coder:7b")
    assert r.ok is False and r.reachable is False
    assert "ollama serve" in r.message


def test_preflight_model_missing(monkeypatch):
    monkeypatch.setattr(preflight, "list_local_models", lambda *a, **k: ["gemma:latest"])
    r = preflight.check("http://x", "qwen2.5-coder:7b")
    assert r.ok is False and r.reachable is True and r.model_present is False
    assert "ollama pull qwen2.5-coder:7b" in r.message
    assert "gemma:latest" in r.message  # available list shown


def test_preflight_ok(monkeypatch):
    monkeypatch.setattr(preflight, "list_local_models",
                        lambda *a, **k: ["qwen2.5-coder:7b"])
    r = preflight.check("http://x", "qwen2.5-coder:7b")
    assert r.ok is True and r.model_present is True


def test_preflight_latest_tag_normalization(monkeypatch):
    # Configured bare, listed with :latest → matches.
    monkeypatch.setattr(preflight, "list_local_models", lambda *a, **k: ["gemma:latest"])
    assert preflight.check("http://x", "gemma").ok is True
    # Configured :latest, listed bare → matches.
    monkeypatch.setattr(preflight, "list_local_models", lambda *a, **k: ["gemma"])
    assert preflight.check("http://x", "gemma:latest").ok is True


# --- actionable errors + base-url normalization ------------------------------

def test_actionable_connection_refused():
    out = cli._actionable("Could not connect to http://localhost:11434: Connection refused")
    assert "ollama serve" in out


def test_actionable_404():
    out = cli._actionable("HTTP 404 from .../api/chat: model 'x' not found")
    assert "ollama pull" in out


def test_actionable_leaves_other_errors_alone():
    assert cli._actionable("some unrelated error") == "some unrelated error"


def test_normalize_base_url_adds_scheme():
    assert cli._normalize_base_url("127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert cli._normalize_base_url("http://host:11434") == "http://host:11434"
    assert cli._normalize_base_url("") == cli._DEFAULT_BASE_URL


def test_default_base_url_honors_ollama_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
    assert cli._default_base_url() == "http://127.0.0.1:9999"
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert cli._default_base_url() == cli._DEFAULT_BASE_URL

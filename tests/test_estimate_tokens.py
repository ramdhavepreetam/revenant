"""Tests for estimate_tokens (F4): real tokenizer with a heuristic fallback.

The key contract is that context-budget code can rely on estimate_tokens whether
or not tiktoken is installed — the tiktoken path when available, the word-count
heuristic otherwise — and that the signature stays str -> int so its two callers
(local_llm_writer + agent_loop) are untouched.
"""
from __future__ import annotations

import nerva_core.local_llm_writer as llm
from nerva_core.local_llm_writer import estimate_tokens, _heuristic_tokens


def test_empty_string_is_at_least_one():
    assert estimate_tokens("") == 1


def test_returns_positive_int():
    n = estimate_tokens("the quick brown fox jumps over the lazy dog")
    assert isinstance(n, int)
    assert n >= 1


def test_longer_text_counts_more():
    short = estimate_tokens("hello world")
    long = estimate_tokens("hello world " * 50)
    assert long > short


def test_uses_tiktoken_when_available(monkeypatch):
    """When an encoder is present, encode() drives the count, not the heuristic."""

    class FakeEncoder:
        def encode(self, text: str):
            return list(range(7))  # pretend exactly 7 tokens

    monkeypatch.setattr(llm, "_ENCODER", FakeEncoder())
    assert estimate_tokens("anything at all") == 7


def test_falls_back_to_heuristic_when_tiktoken_absent(monkeypatch):
    """`False` marks tiktoken as tried-and-unavailable; the heuristic is used."""
    monkeypatch.setattr(llm, "_ENCODER", False)
    text = "one two three four five"
    assert estimate_tokens(text) == _heuristic_tokens(text)


def test_falls_back_when_encoder_raises(monkeypatch):
    """A broken encoder must not crash budgeting — degrade to the heuristic."""

    class ExplodingEncoder:
        def encode(self, text: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(llm, "_ENCODER", ExplodingEncoder())
    text = "alpha beta gamma"
    assert estimate_tokens(text) == _heuristic_tokens(text)


def test_get_encoder_caches(monkeypatch):
    """_get_encoder memoizes so we don't re-import tiktoken on every call."""
    monkeypatch.setattr(llm, "_ENCODER", None)
    first = llm._get_encoder()
    second = llm._get_encoder()
    assert first is second

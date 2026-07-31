"""Revenant's offline eval harness (H0, ADR-0015).

Measures harness lift on a fixed model: a small suite of self-contained coding
tasks (`evals/tasks/`), each with a deterministic scorer, driven end-to-end by
`evals/run.py`. The harness's own logic is unit-tested model-free in
`tests/test_evals.py`; actual model-driven runs are opt-in (`python evals/run.py
--model ...`), never part of `pytest`.
"""
from __future__ import annotations

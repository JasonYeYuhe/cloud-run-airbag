"""Airbag-Bench — a labeled incident-replay harness + scorecard (Phase 0.1).

Importable as the top-level package ``bench`` under pytest (because ``agent/tests/`` has no
``__init__.py``, pytest inserts ``agent/tests`` on sys.path, so ``tests/bench`` resolves as ``bench``).
The CLI (``run_bench.py``) bootstraps sys.path explicitly so it also works under direct execution.
"""
from __future__ import annotations

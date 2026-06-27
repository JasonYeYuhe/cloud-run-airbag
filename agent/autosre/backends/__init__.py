"""Execution backends. The active one is selected by AIRBAG_BACKEND.

A backend exposes:
  list_cloud_run_revisions(service, region) -> dict
  query_error_rate(service, region, window_minutes) -> dict
  synthetic_probe(service, path) -> dict
  rollback_traffic_to_revision(service, region, revision) -> dict
  restore_traffic_to_latest(service, region) -> dict
"""
from __future__ import annotations

import importlib

from .. import config

_BACKENDS = {"mock": "mock", "local": "local", "gcp": "gcp"}


def get_backend():
    name = _BACKENDS.get(config.BACKEND, "mock")
    return importlib.import_module(f"autosre.backends.{name}")

"""Target demo app (deploys to Cloud Run).

A healthy service with an injectable fault so we can reproduce "a bad revision
shipped and starts erroring" \u2014 including the 'delay bomb' (errors begin only N
seconds after start, i.e. outside the deploy/canary window).

Fault sources:
  - env FAULT_MODE = off | http500 | delay_bomb  (a 'bad revision' ships with this)
  - runtime POST /__fault/{mode}                 (demo harness: flip faults live)
/healthz stays 200 so Cloud Run readiness and the agent's synthetic probe work.
"""
from __future__ import annotations

import os
import time

from fastapi import FastAPI, Response

app = FastAPI(title="airbag-target")
_START = time.time()
FAULT_MODE = os.getenv("FAULT_MODE", "off")
DELAY_BOMB_AFTER_S = float(os.getenv("DELAY_BOMB_AFTER_S", "90"))
_runtime: dict[str, str | None] = {"fault": None}


def _active_fault() -> str | None:
    if _runtime["fault"] is not None:
        return _runtime["fault"] or None
    if FAULT_MODE == "delay_bomb" and (time.time() - _START) >= DELAY_BOMB_AFTER_S:
        return "http500"
    return FAULT_MODE if FAULT_MODE == "http500" else None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


ORDERS = [{"id": 1, "price": 10}, {"id": 2, "price": 25}]


def total_revenue(orders, buggy=False):
    # A "bad deploy" ships buggy=True, which reads a key that doesn't exist on the order
    # dicts -> KeyError -> HTTP 500. The fix is to read the correct "price" key.
    key = "price" # Always use the correct "price" key
    return sum(o[key] for o in orders)


@app.get("/api/orders")
def orders():
    fault = _active_fault()
    if fault == "http500":
        return Response(status_code=500, content='{"error":"simulated outage"}',
                        media_type="application/json")
    return {"orders": ORDERS, "revenue": total_revenue(ORDERS, buggy=(fault == "bug"))}


@app.post("/__fault/{mode}")
def set_fault(mode: str):
    """Demo harness: mode in {off, http500}."""
    _runtime["fault"] = None if mode == "off" else mode
    return {"fault": _runtime["fault"]}

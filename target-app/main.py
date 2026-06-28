from __future__ import annotations

import os
import time

from fastapi import FastAPI, Response

app = FastAPI(title="airbag-target")
_START = time.time()
FAULT_MODE = os.getenv("FAULT_MODE", "off")
DELAY_BOMB_AFTER_S = float(os.getenv("DELAY_BOMB_AFTER_S", "90"))
_FAULTS = ("bug", "http500")
_runtime: dict[str, str | None] = {"fault": None}


def _active_fault() -> str | None:
    if _runtime["fault"] is not None:
        return _runtime["fault"] or None
    # a 'delay bomb' revision ships clean, then trips the bug N seconds in (out-of-window)
    if FAULT_MODE == "delay_bomb":
        return "bug" if (time.time() - _START) >= DELAY_BOMB_AFTER_S else None
    return FAULT_MODE if FAULT_MODE in _FAULTS else None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


ORDERS = [{"id": 1, "price": 10}, {"id": 2, "price": 25}]


def total_revenue(orders, buggy=False):
    # A "bad deploy" ships buggy=True, which reads a key that doesn't exist on the order
    # dicts -> KeyError -> HTTP 500. The fix is to read the correct "price" key.
    key = "price"
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
    """Demo harness: mode in {off, bug, http500}. `bug` triggers the KeyError that the
    Gemini fix-PR repairs; `off` clears the fault (= healthy revision)."""
    _runtime["fault"] = None if mode == "off" else mode
    return {"fault": _runtime["fault"]}

"""Target demo app (deploys to Cloud Run).

A healthy service with an injectable fault so we can reproduce "a bad revision
shipped and starts erroring" — including the 'delay bomb' (errors begin only N
seconds after start, i.e. outside the deploy/canary window).

Fault sources:
  - env FAULT_MODE = off | bug | http500 | slow | delay_bomb  (a 'bad revision' ships with this)
  - runtime POST /__fault/{mode}                              (demo harness: flip faults live)
The canonical demo fault is `bug`: a real KeyError in total_revenue() (reads "amount"
instead of "price") -> unhandled exception -> HTTP 500. This is the SAME root-cause bug
the Gemini fix-PR repairs, so the story is coherent: we roll back the bad revision AND
open a PR that fixes the exact cause. `http500` is a blunt alternative (explicit 500).
`slow` is the v3 LATENCY regression: /api/orders still returns 200, just slowly (> the
latency SLO) with ~0 5xx — the canonical out-of-window regression a 5xx-only monitor
MISSES but Airbag's multi-signal latency detector catches.
Airbag probes the BUSINESS path (AIRBAG_PROBE_PATH, default /api/orders) for detection,
recovery + latency — NOT /healthz — so the `slow` fault is actually observed. Cloud Run
readiness uses a TCP startup probe (port 8080); /healthz is just a fast liveness endpoint.
"""
from __future__ import annotations

import hmac
import os
import time

from fastapi import FastAPI, Request, Response

app = FastAPI(title="airbag-target")
_START = time.time()
FAULT_MODE = os.getenv("FAULT_MODE", "off")
# Token to gate the runtime fault toggle so a public --allow-unauthenticated target can't be
# griefed. Empty -> open (local dev). The gcp demo breaks via revision routing, not /__fault.
FAULT_TOKEN = os.getenv("FAULT_TOKEN", "")
DELAY_BOMB_AFTER_S = float(os.getenv("DELAY_BOMB_AFTER_S", "90"))
# `slow` fault: delay /api/orders past the latency SLO (agent default AIRBAG_LATENCY_SLO_ABS_MS=800),
# so it's a confident latency regression with ~0 5xx. /healthz stays fast (Cloud Run readiness).
SLOW_DELAY_S = float(os.getenv("SLOW_DELAY_S", "2.0"))
_FAULTS = ("bug", "http500", "slow")
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
    key = "amount" if buggy else "price"
    return sum(o[key] for o in orders)


@app.get("/api/orders")
def orders():
    fault = _active_fault()
    if fault == "http500":
        return Response(status_code=500, content='{"error":"simulated outage"}',
                        media_type="application/json")
    if fault == "slow":                 # v3 latency regression: still 200, just past the SLO
        time.sleep(SLOW_DELAY_S)
    return {"orders": ORDERS, "revenue": total_revenue(ORDERS, buggy=(fault == "bug"))}


@app.post("/__fault/{mode}")
def set_fault(mode: str, request: Request):
    """Demo harness: mode in {off, bug, http500, slow}. `bug` triggers the KeyError that the
    Gemini fix-PR repairs; `slow` is the v3 latency regression; `off` clears the fault
    (= healthy revision). Token-gated when
    FAULT_TOKEN is set so the public target can't be toggled by anyone."""
    if FAULT_TOKEN:
        supplied = request.headers.get("x-fault-token") or request.query_params.get("token", "")
        if not (supplied and hmac.compare_digest(supplied, FAULT_TOKEN)):
            return Response(status_code=401, content='{"error":"invalid fault token"}',
                            media_type="application/json")
    _runtime["fault"] = None if mode == "off" else mode
    return {"fault": _runtime["fault"]}

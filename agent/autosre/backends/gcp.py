"""GCP backend — real Cloud Run + Cloud Logging. Behind AIRBAG_BACKEND=gcp.

Shapes hardened per code review (Codex + Gemini): real Condition.State enum compare,
short revision names, region-scoped log filter, post-rollback verify window, business-
path probe, update_mask on traffic updates. Verified end-to-end against the live project
(airbag-hack-260628 / asia-northeast1): detect → rollback → prove recovery → fix PR →
verify-and-undo, all on real Cloud Run.
"""
from __future__ import annotations

import datetime
import time

import httpx

from .. import config

# Optimistic-concurrency retries for a traffic mutation (see _set_traffic).
_TRAFFIC_MAX_ATTEMPTS = 4


def _service_path(service: str, region: str) -> str:
    return f"projects/{config.GCP_PROJECT}/locations/{region}/services/{service}"


def _get_service(service: str, region: str):
    from google.cloud import run_v2
    return run_v2.ServicesClient().get_service(name=_service_path(service, region))


def _short(name: str | None) -> str | None:
    return name.split("/")[-1] if name else name


def list_cloud_run_revisions(service: str, region: str) -> dict:
    from google.cloud import run_v2

    svc = _get_service(service, region)
    # Traffic can be pinned to an explicit revision OR to LATEST (revision='' on the
    # status). LATEST means "the newest revision", so resolve it after sorting.
    explicit: dict = {}
    latest_percent = 0
    for t in svc.traffic_statuses:
        if t.revision:
            explicit[_short(t.revision)] = t.percent
        elif t.type_ == run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST:
            latest_percent += t.percent
    revs = []
    for r in run_v2.RevisionsClient().list_revisions(parent=_service_path(service, region)):
        name = _short(r.name)
        ready = any(c.type_ == "Ready" and c.state == run_v2.Condition.State.CONDITION_SUCCEEDED
                    for c in r.conditions)
        revs.append({"name": name, "ready": ready,
                     "traffic_percent": explicit.get(name, 0),
                     "create_time": r.create_time.isoformat() if r.create_time else None})
    revs.sort(key=lambda x: x.get("create_time") or "", reverse=True)
    if latest_percent and revs:
        revs[0]["traffic_percent"] += latest_percent  # LATEST == newest revision
    return {"service": service, "revisions": revs, "uri": svc.uri}


def query_error_rate(service: str, region: str, window_minutes: int = 5,
                     since_epoch: float | None = None) -> dict:
    """5xx presence from Cloud Logging. `since_epoch` (set during verify) anchors the
    window at rollback time so we don't keep counting pre-rollback errors. Coarse
    has-errors gate is enough for recovery; the synthetic probe is the active signal."""
    from google.cloud import logging as cloud_logging

    if since_epoch:
        start = datetime.datetime.fromtimestamp(since_epoch, datetime.timezone.utc)
    else:
        start = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=window_minutes))
    flt = (f'resource.type="cloud_run_revision" '
           f'resource.labels.service_name="{service}" '
           f'resource.labels.location="{region}" '
           f'httpRequest.status>=500 timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    errs = sum(1 for _ in client.list_entries(
        filter_=flt, resource_names=[f"projects/{config.GCP_PROJECT}"], max_results=50))
    # Cloud Logging ingestion lags ~10-20s. For live detection (triage, since_epoch=None) don't
    # miss a current outage that hasn't been ingested yet — actively sample the business path.
    # The post-rollback verify (since_epoch set) stays log-based so "recovery" is proven by logs.
    if errs == 0 and since_epoch is None:
        s_errs, s_total = _active_sample(service)
        if s_errs:
            return {"service": service, "error_rate": round(s_errs / s_total, 3),
                    "errors": s_errs, "total_requests": s_total,
                    "window_minutes": window_minutes, "source": "active-probe"}
    return {"service": service, "error_rate": 1.0 if errs else 0.0,
            "errors": errs, "total_requests": None, "window_minutes": window_minutes,
            "source": "cloud-logging"}


def _active_sample(service: str, n: int = 8) -> tuple[int, int]:
    """Hit the live business path n times, counting 5xx (incl. connection failures)."""
    try:
        uri = _get_service(service, config.GCP_REGION).uri.rstrip("/") + config.PROBE_PATH
    except Exception:  # noqa: BLE001
        return 0, 0
    errs = 0
    with httpx.Client(timeout=5.0) as c:
        for _ in range(n):
            try:
                if c.get(uri).status_code >= 500:
                    errs += 1
            except Exception:  # noqa: BLE001
                errs += 1
    return errs, n


def fetch_error_logs(service: str, region: str, n: int = 10) -> list[str]:
    """Recent ERROR-severity log entries (Cloud Run logs unhandled exceptions + tracebacks to
    stderr at severity=ERROR) — the real stack trace the RCA agent reads."""
    import json as _json

    from google.cloud import logging as cloud_logging

    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=15))
    flt = (f'resource.type="cloud_run_revision" '
           f'resource.labels.service_name="{service}" '
           f'resource.labels.location="{region}" severity>=ERROR '
           f'timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"')
    client = cloud_logging.Client(project=config.GCP_PROJECT)
    out: list[str] = []
    try:
        for e in client.list_entries(filter_=flt, resource_names=[f"projects/{config.GCP_PROJECT}"],
                                     order_by=cloud_logging.DESCENDING, max_results=n):
            p = getattr(e, "payload", None)
            txt = p if isinstance(p, str) else _json.dumps(p, default=str)
            if txt:
                out.append(txt[:2000])
    except Exception as ex:  # noqa: BLE001
        out.append(f"(log fetch failed: {ex})")
    return out


def sample_business_path(service: str, region: str, n: int = 20) -> dict:
    errs, total = _active_sample(service, n=n)
    return {"errs": errs, "total": total}


def sample_latency_windows(service: str, region: str, windows: int = 4, per_window: int = 8) -> list[dict]:
    """Per-window count of business-path requests slower than the latency SLO (max(baseline p99 ×
    factor, ABS_MS)), sampled in `windows` bursts — the multi-window persistence the latency detector
    debounces on. Actively samples (like query_error_rate's active probe) rather than querying a Cloud
    Monitoring distribution (the monitoring client isn't a dep). OPT-IN: only called when
    AIRBAG_SIGNALS includes 'latency'; the demo runs 5xx-only. Defensive — benign (no data) on error,
    which reads as INCONCLUSIVE (never a false trigger)."""
    import time as _time
    try:
        uri = _get_service(service, region).uri.rstrip("/") + config.PROBE_PATH
    except Exception:  # noqa: BLE001
        return [{"slow": 0, "total": 0} for _ in range(windows)]
    slo_ms = config.LATENCY_SLO_ABS_MS  # absolute SLO floor (baseline-relative refinement is a follow-up)
    out: list[dict] = []
    with httpx.Client(timeout=10.0) as c:
        for _ in range(windows):
            slow = total = 0
            for _ in range(per_window):
                t0 = _time.monotonic()
                try:
                    r = c.get(uri)
                    dt_ms = (_time.monotonic() - t0) * 1000.0
                    total += 1
                    if r.status_code < 500 and dt_ms > slo_ms:  # a slow SUCCESS is the latency signal
                        slow += 1
                except Exception:  # noqa: BLE001 — a timeout / connection failure is a degraded request
                    total += 1
                    slow += 1
            out.append({"slow": slow, "total": total})
    return out


_CAUSAL_TAG = "airbagcausal"


def probe_revision_health(service: str, region: str, revision: str, n: int = 8) -> dict:
    """Probe the rollback TARGET (a non-serving revision) directly for the causal pre-check. Tags it
    at 0% traffic (NON-disruptive — serving traffic is untouched; NOT set_traffic_split, which drops
    0% entries) to get a per-revision URL, probes it n times, then RESTORES the original traffic in a
    finally. Returns {errs, total, slow} — `slow` (v4 Phase 2) counts SUCCESSFUL responses over the
    latency SLO, timed per-request, so the causal check can veto a still-slow target for a LATENCY
    incident. Defensive: ANY error → {errs:0, total:0, slow:0}, which causal.py reads as
    INCONCLUSIVE → PROCEED with the rollback — a probe failure must never block a legitimate rollback.
    OPT-IN (AIRBAG_CAUSAL_CHECK); the demo runs with the causal check off."""
    from google.cloud import run_v2
    original = None
    try:
        svc = _get_service(service, region)
        original = list(svc.traffic)   # preserve the exact serving split to restore
        probe_targets = list(svc.traffic) + [run_v2.TrafficTarget(
            type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
            revision=revision, percent=0, tag=_CAUSAL_TAG)]
        _set_traffic(service, region, probe_targets)   # add a 0% tag → target gets a stable URL, no traffic
        svc2 = _get_service(service, region)
        uri = next((t.uri for t in svc2.traffic_statuses
                    if getattr(t, "tag", "") == _CAUSAL_TAG and getattr(t, "uri", "")), None)
        if not uri:
            return {"errs": 0, "total": 0, "slow": 0}
        full = uri.rstrip("/") + config.PROBE_PATH
        slo_ms = config.LATENCY_SLO_ABS_MS
        errs = ok = slow = 0
        with httpx.Client(timeout=10.0) as c:
            # WARMUP RINSE (Phase-2 review, MAJOR): the target is scaled to zero (0% traffic), so
            # the first request systematically includes instance boot + new-tag-hostname TLS — a
            # slow SUCCESS that is cold start, not latency evidence. One untimed, uncounted request
            # enforces "cold start is not evidence" on the LATENCY axis (the except-drop below
            # already enforces it on the reachability axis). Without it, 3/8 warmup-slow samples
            # Wilson-FAIL and would falsely veto a legitimate latency rollback — the worst failure.
            try:
                c.get(full)
            except Exception:  # noqa: BLE001 — the rinse is best-effort by definition
                pass
            for _ in range(n):
                t0 = time.monotonic()
                try:
                    r = c.get(full)
                    dt_ms = (time.monotonic() - t0) * 1000.0
                    if r.status_code >= 500:
                        errs += 1   # a 5xx STATUS is evidence the target served-and-broke
                    elif dt_ms > slo_ms:
                        slow += 1   # a slow SUCCESS is the latency-axis evidence (mirrors
                                    # sample_latency_windows: 5xx and slow are disjoint counts)
                    ok += 1
                except Exception:  # noqa: BLE001
                    # UNREACHABLE ≠ degraded: a cold start on the scaled-to-zero last-good target (it
                    # has 0% traffic) or a transient timeout is NO evidence of failure — DROP the
                    # sample. Counting it would falsely block a legitimate rollback (the worst
                    # failure). HONEST LIMIT: this also bounds the latency veto to slowness UNDER the
                    # 10s client timeout — a >10s-slow target reads as unreachable → INCONCLUSIVE →
                    # proceed (pre-Phase-2 behavior; _verify still catches it post-shift). Counting
                    # ReadTimeouts as slow would make an 8/8-timeout cold start a confident false
                    # COINCIDENT, which is the worse trade.
                    pass
        return {"errs": errs, "total": ok, "slow": slow}   # all-unreachable -> INCONCLUSIVE -> proceed
    except Exception:  # noqa: BLE001 — never let a causal-probe failure block a rollback
        return {"errs": 0, "total": 0, "slow": 0}
    finally:
        if original is not None:
            try:
                _set_traffic(service, region, original)   # restore serving split + drop the causal tag
            except Exception:  # noqa: BLE001
                pass


def synthetic_probe(service: str, path: str | None = None) -> dict:
    path = path or config.PROBE_PATH
    try:
        uri = _get_service(service, config.GCP_REGION).uri
        with httpx.Client(timeout=10.0) as c:  # > the slow-fault delay so a slow SUCCESS is timed, not dropped
            t0 = time.monotonic()
            r = c.get(uri.rstrip("/") + path)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
        return {"ok": r.status_code == 200, "path": path, "status": r.status_code,
                "elapsed_ms": round(elapsed_ms, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": path, "status": 0, "error": str(e)}


def _set_traffic(service: str, region: str, targets):
    """Set the service's traffic split with OPTIMISTIC CONCURRENCY.

    Under --max-instances 3 two instances can race a traffic write — a heal's rollback (claim_heal
    lease) against a complete_rollback's canary (a SEPARATE lease), which the leases do not serialize.
    A plain read-modify-write would let last-writer-wins strand a torn/partial split or silently undo
    a fresh rollback. So we read a FRESH service (and its etag — the concurrency token, carried inside
    the Service) on EVERY attempt, re-apply the INTENDED targets, and let Cloud Run reject a stale
    write; on that conflict we re-read + retry. Each write therefore lands as one of the intended
    whole states, never a torn mix, and a transient conflict doesn't fail the whole heal."""
    from google.api_core import exceptions as gax
    from google.cloud import run_v2
    targets = targets if isinstance(targets, list) else [targets]
    client = run_v2.ServicesClient()
    last_exc: Exception | None = None
    for attempt in range(_TRAFFIC_MAX_ATTEMPTS):
        svc = _get_service(service, region)          # fresh etag per attempt (optimistic-concurrency token)
        svc.traffic = targets
        try:
            client.update_service(
                service=svc, update_mask={"paths": ["traffic"]}).result(timeout=120)
            return
        except (gax.Aborted, gax.FailedPrecondition) as e:  # a concurrent writer changed the service
            last_exc = e
            time.sleep(0.25 * (attempt + 1))
    raise last_exc  # exhausted retries -> surface so the heal releases its lease and a retry re-runs


def rollback_traffic_to_revision(service: str, region: str, revision: str) -> dict:
    from google.cloud import run_v2
    _set_traffic(service, region, run_v2.TrafficTarget(
        type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=revision, percent=100))
    return {"status": "success", "service": service, "active_revision": revision}


_CANARY_TAG = "airbagfix"


def set_traffic_split(service: str, region: str, splits: dict, tag_revision: str | None = None) -> dict:
    """Split 100% of traffic across explicit revisions, e.g. {fix: 10, safe: 90} for a canary.
    Optionally TAG one revision so it gets a stable per-revision URL we can probe directly
    (so the canary gate verifies the fix itself, not the load-balanced service)."""
    from google.cloud import run_v2
    targets = []
    for rev, pct in splits.items():
        if int(pct) <= 0:
            continue
        t = run_v2.TrafficTarget(
            type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
            revision=rev, percent=int(pct))
        if rev == tag_revision:
            t.tag = _CANARY_TAG
        targets.append(t)
    _set_traffic(service, region, targets)
    return {"status": "success", "service": service,
            "traffic": {r: int(p) for r, p in splits.items() if int(p) > 0}}


def probe_candidate(service: str, region: str, revision: str, n: int = 6) -> dict:
    """Probe the candidate revision DIRECTLY via its per-revision tag URL (not the load-balanced
    service URL) — so a bad fix is caught even at a low canary percentage, where the service URL
    would almost always route to the healthy revision instead."""
    try:
        svc = _get_service(service, region)
        uri = next((t.uri for t in svc.traffic_statuses
                    if getattr(t, "uri", "") and (getattr(t, "tag", "") == _CANARY_TAG
                                                  or _short(getattr(t, "revision", "")) == revision)), None)
        uri = uri or svc.uri  # last resort: load-balanced service URL
        full = uri.rstrip("/") + config.PROBE_PATH
        errs = 0
        with httpx.Client(timeout=5.0) as c:
            for _ in range(n):
                try:
                    if c.get(full).status_code >= 500:
                        errs += 1
                except Exception:  # noqa: BLE001
                    errs += 1
        return {"ok": errs == 0, "errors": errs, "total": n, "url": uri}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "errors": n, "total": n, "error": str(e)}


# --- demo harness: drive break/reset by routing traffic between revisions -----------
_FAULT_VALUES = {"bug", "http500", "delay_bomb", "slow"}   # "slow" = the v3 latency regression


def _revision_env(rev) -> dict:
    """FAULT_MODE etc. baked into a revision's container env (skips secret refs)."""
    env: dict = {}
    for c in getattr(rev, "containers", []) or []:
        for e in getattr(c, "env", []) or []:
            if e.name:
                env[e.name] = e.value
    return env


def _ready_revisions_newest_first(service: str, region: str):
    from google.cloud import run_v2
    revs = list(run_v2.RevisionsClient().list_revisions(parent=_service_path(service, region)))
    ready = [r for r in revs if any(
        c.type_ == "Ready" and c.state == run_v2.Condition.State.CONDITION_SUCCEEDED
        for c in r.conditions)]
    ready.sort(key=lambda r: r.create_time.isoformat() if r.create_time else "", reverse=True)
    return ready


def break_target(service: str, region: str, prefer: str = "bug") -> dict:
    """Route 100% traffic to a bad revision. Prefer FAULT_MODE=`prefer` (default 'bug' — the KeyError
    the fix-PR repairs; 'slow' selects the v3 latency-regression revision) over any other fault
    revision, so each demo picks its intended fault."""
    ready = _ready_revisions_newest_first(service, region)
    pref = next((r for r in ready if _revision_env(r).get("FAULT_MODE") == prefer), None)
    bad = pref or next((r for r in ready if _revision_env(r).get("FAULT_MODE") in _FAULT_VALUES), None)
    if not bad:
        return {"status": "error", "service": service,
                "error": f"no fault-carrying revision found (prefer={prefer}); deploy one with "
                         f"FAULT_MODE={prefer} --no-traffic (see deploy.sh / scripts/gcp-demo.sh)"}
    name = _short(bad.name)
    rollback_traffic_to_revision(service, region, name)
    return {"status": "success", "service": service, "active_revision": name,
            "fault": _revision_env(bad).get("FAULT_MODE")}


def reset_target(service: str, region: str) -> dict:
    """Route 100% traffic to the newest healthy (no-fault) revision."""
    ready = _ready_revisions_newest_first(service, region)
    good = next((r for r in ready if _revision_env(r).get("FAULT_MODE") not in _FAULT_VALUES), None)
    if not good:
        return {"status": "error", "service": service, "error": "no healthy revision found"}
    name = _short(good.name)
    rollback_traffic_to_revision(service, region, name)
    return {"status": "success", "service": service, "active_revision": name}

"""Render a persisted incident record as a self-contained HTML "incident report" Artifact —
the verifiable thought-chain a judge or on-call human can audit: the decision, the signals,
before/after metrics, the fix PR, and the full timeline. No JS, no external assets."""
from __future__ import annotations

import datetime
import html

_STAGE_COLOR = {
    "RUN_START": "#6b7a8d", "RECEIVED": "#f85149", "FAULT_INJECTED": "#f85149",
    "TRIAGED": "#58a6ff", "ANALYZED": "#58a6ff", "ADK": "#bc8cff", "DECISION": "#bc8cff",
    "ROLLBACK_APPLIED": "#58a6ff", "VERIFYING": "#e3b341", "MITIGATED": "#3fb950",
    "FIX_PR": "#6b7a8d", "PENDING_REVERT": "#e3b341", "ESCALATED": "#e3b341",
    "COMPLETE_ROLLBACK": "#e3b341", "FIX_DEPLOYED": "#58a6ff", "REVERIFYING": "#e3b341",
    "CANARY": "#e3b341",
    "ROLLBACK_UNDONE": "#3fb950", "CLOSED": "#3fb950", "MANUAL_INTERVENTION": "#f85149",
    "DONE": "#6b7a8d", "CI_WATCH": "#e3b341", "CI_RED": "#e3b341", "CI_CORRECTED": "#e3b341",
    "CI_GREEN": "#3fb950", "CI_ESCALATED": "#f85149",
    "OBSERVE_ONLY": "#6b7a8d", "AWAITING_APPROVAL": "#e3b341", "APPROVED": "#3fb950",
    "DENIED": "#f85149", "AUTONOMY": "#bc8cff", "RECURRING": "#e3b341",
}
_STATUS_COLOR = {"mitigated": "#3fb950", "closed": "#3fb950", "noop": "#6b7a8d",
                 "escalated": "#e3b341", "compensated": "#e3b341", "manual_intervention": "#f85149",
                 "observed": "#6b7a8d", "awaiting_approval": "#e3b341",
                 "awaiting_fix_approval": "#e3b341", "rollback_denied": "#f85149",
                 "fix_pr_denied": "#f85149"}


def _ts(t) -> str:
    if not t:
        return ""
    try:
        return datetime.datetime.fromtimestamp(float(t), datetime.timezone.utc).strftime("%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _find(events, stage):
    return next((e for e in events if e.get("stage") == stage), None)


def _recovery_seconds(events) -> float | None:
    """Alert-to-Verified-Recovery time (the v3 headline metric): from the incident's first stage to
    the proven recovery (MITIGATED) or the closed transaction (ROLLBACK_UNDONE/CLOSED)."""
    start = next((e.get("ts") for e in events
                  if e.get("stage") in ("FAULT_INJECTED", "RECEIVED", "RUN_START")), None)
    end = next((e.get("ts") for e in reversed(events)
                if e.get("stage") in ("MITIGATED", "ROLLBACK_UNDONE", "CLOSED")), None)
    try:
        return float(end) - float(start) if (start and end and float(end) >= float(start)) else None
    except (TypeError, ValueError):
        return None


def render(rec: dict) -> str:
    iid = _esc(rec.get("incident_id"))
    status = rec.get("status", "—")
    sc = _STATUS_COLOR.get(status, "#6b7a8d")
    d = rec.get("decision") or {}
    pr = rec.get("pr_url")
    # only render as a link for http(s) (avoid javascript:/data: href injection); else plain text
    if pr and str(pr).lower().startswith(("http://", "https://")):
        pr_html = f'<a href="{_esc(pr)}" rel="noopener">{_esc(pr)}</a>'
    else:
        pr_html = _esc(pr) or "—"
    eb, ea = rec.get("error_before"), rec.get("error_after")

    rows = "".join(
        f'<tr><td class="t">{_ts(e.get("ts"))}</td>'
        f'<td class="s" style="color:{_STAGE_COLOR.get(e.get("stage"), "#c9d4e0")}">{_esc(e.get("stage"))}</td>'
        f'<td class="m">{_esc(e.get("msg"))}</td></tr>'
        for e in rec.get("events", []))

    evidence = d.get("evidence") or []
    evidence_html = "".join(f"<li>{_esc(x)}</li>" for x in evidence) or "<li>—</li>"
    tools = d.get("_adk_tools")

    # --- v3 legibility: surface the multi-signal verdict + per-detector breakdown + causal pre-check
    # (extracted from the already-emitted ANALYZED/CAUSAL events — works whether or not the extra
    # signals/causal are enabled; in the default 5xx config it just shows the single Wilson verdict) ---
    events = rec.get("events", [])
    an, cz = _find(events, "ANALYZED"), _find(events, "CAUSAL")
    detect_html = ""
    if an:
        sig = an.get("signals")
        if isinstance(sig, dict) and sig:
            det = "".join(
                f'<div class="kv"><span>{_esc(k)}</span>'
                f'<span class="mono">{_esc((v or {}).get("verdict"))} — {_esc((v or {}).get("reason"))}</span></div>'
                for k, v in sig.items())
        else:
            det = '<div class="kv"><span>5xx</span><span class="mono">' \
                  f'{_esc(an.get("rate"))} rate</span></div>'
        detect_html = (
            '<div class="card"><h2>Detection — multi-signal verdict</h2>'
            f'<div class="act" style="color:#e3b341">{_esc(an.get("verdict"))}</div>'
            f'<div style="margin:8px 0">{_esc(an.get("reason"))}</div>{det}</div>')
    causal_html = ""
    if cz:
        causal_html = (
            '<div class="card"><h2>Causal pre-check</h2>'
            f'<div class="act" style="color:#bc8cff">{_esc(cz.get("verdict"))}</div>'
            f'<div style="margin:8px 0">{_esc(cz.get("msg"))}</div>'
            '<div style="color:#6b7a8d;font-size:12px">probed the rollback target before committing — '
            'a rollback is spent only when it will actually help</div></div>')
    v3_grid = (f'<div class="grid">{detect_html}{causal_html}</div>'
               if (detect_html or causal_html) else "")
    recovery_s = _recovery_seconds(events)
    recovery_html = (f'<div class="kv"><span>alert → verified recovery</span>'
                     f'<span class="mono" style="color:#3fb950">{recovery_s:.0f}s</span></div>'
                     if recovery_s is not None else "")
    # tamper-evident proof digest (lazy import: proof imports report._recovery_seconds)
    try:
        from . import proof as _proof
        _digest = _esc(_proof.build(rec).get("digest", ""))
    except Exception:  # noqa: BLE001 — the report must render even if the digest can't be built
        _digest = ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Airbag incident {iid}</title><style>
 body{{margin:0;background:#0a0e14;color:#c9d4e0;font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;padding:24px}}
 .wrap{{max-width:960px;margin:0 auto}}
 h1{{font-size:18px;margin:0 0 4px}} h1 b{{color:#3fb950}}
 .mono{{font-family:SFMono-Regular,ui-monospace,Menlo,Consolas,monospace}}
 .badge{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700;
   color:#0a0e14;background:{sc}}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:18px 0}}
 .card{{background:#0e141d;border:1px solid #1d2733;border-radius:12px;padding:14px}}
 .card h2{{margin:0 0 10px;font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:#6b7a8d}}
 .kv{{display:flex;justify-content:space-between;gap:12px;margin:5px 0}} .kv span:first-child{{color:#6b7a8d}}
 .act{{font-size:16px;font-weight:800;color:#58a6ff}} .chip{{color:#bc8cff;font-size:12px}}
 table{{width:100%;border-collapse:collapse;font-family:SFMono-Regular,ui-monospace,Menlo,monospace;font-size:12.5px}}
 td{{padding:7px 8px;border-bottom:1px solid #141c26;vertical-align:top}}
 td.t{{color:#6b7a8d;white-space:nowrap;width:74px}} td.s{{font-weight:700;width:160px}}
 a{{color:#58a6ff}} ul{{margin:6px 0;padding-left:18px}} .foot{{color:#6b7a8d;font-size:12px;margin-top:18px}}
</style></head><body><div class="wrap">
 <h1>🛟 <b>Airbag</b> — incident report</h1>
 <div class="mono" style="color:#6b7a8d">{iid} · {_esc(rec.get("service"))} · <span class="badge">{_esc(status).upper()}</span></div>
 <div class="grid">
  <div class="card"><h2>Decision (Gemini via ADK)</h2>
   <div class="act">{_esc(d.get("action") or "—")} <span class="chip">conf {_esc(d.get("confidence"))} · {_esc(d.get("_source") or "—")}</span></div>
   <div style="margin:8px 0">{_esc(d.get("reasoning"))}</div>
   <div class="kv"><span>tools called</span><span class="mono">{_esc(tools) if tools else "—"}</span></div>
   <div style="color:#6b7a8d;margin-top:8px">evidence</div><ul>{evidence_html}</ul>
  </div>
  <div class="card"><h2>Proof of recovery</h2>
   {recovery_html}
   <div class="kv"><span>5xx error-rate before</span><span class="mono" style="color:#f85149">{_esc(eb)}</span></div>
   <div class="kv"><span>5xx error-rate after</span><span class="mono" style="color:#3fb950">{_esc(ea)}</span></div>
   <div class="kv"><span>rolled back to</span><span class="mono">{_esc(rec.get("rolled_back_to") or "—")}</span></div>
   <div class="kv"><span>restored to (fix)</span><span class="mono">{_esc(rec.get("restored_to") or "—")}</span></div>
   <div class="kv"><span>fix PR</span><span class="mono">{pr_html}</span></div>
  </div>
 </div>
 {v3_grid}
 <div class="card"><h2>Thought-chain timeline</h2>
  <table><tbody>{rows}</tbody></table>
 </div>
 <div class="foot">Generated by Airbag. Machine-readable JSON: <a href="/incidents/{iid}">/incidents/{iid}</a> ·
  tamper-evident <a href="/incidents/{iid}/proof">proof bundle</a> <span class="mono">{_digest}</span> ·
  the deterministic state machine executed every action; Gemini only decided.</div>
</div></body></html>"""

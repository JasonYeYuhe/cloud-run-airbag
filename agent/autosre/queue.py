"""Durable work queue (AIRBAG_QUEUE = inproc | cloudtasks).

  inproc (default)  — FastAPI BackgroundTasks; today's behavior, zero demo risk, keeps the heal
                      in the request's instance (paired with --max-instances=1).
  cloudtasks        — enqueue a Cloud Tasks HTTP task → POST {SELF_URL}/internal/run-heal
                      {incident_id, service} with the dedicated INTERNAL_TOKEN header. Cloud Tasks
                      redelivers on 5xx/timeout, so the heal survives an instance recycle; the
                      per-incident lease (state_store.claim_heal) makes that at-least-once delivery
                      idempotent, which is what lets the agent safely scale past one instance.

Reviewed (design) by a multi-agent workflow: the worker is idempotent per incident_id, the
dispatch deadline is set >= the worst-case run so a still-running heal isn't redelivered as failed,
and a Cloud Tasks failure falls back to in-process so an enqueue hiccup never drops an incident.
"""
from __future__ import annotations

import json
import logging

from . import config

log = logging.getLogger("airbag.queue")


def enqueue_heal(background_tasks, incident_id: str, service: str) -> str:
    """Dispatch a self-heal. Returns the mode actually used ('cloudtasks' | 'inproc')."""
    if config.QUEUE_BACKEND == "cloudtasks":
        try:
            _enqueue_cloudtask(incident_id, service)
            return "cloudtasks"
        except Exception as e:  # noqa: BLE001 — never drop an incident on an enqueue hiccup
            log.error("cloudtasks enqueue failed (%s); falling back to in-process", e)
    from .state_machine import run_self_heal  # lazy: avoid an import cycle
    if background_tasks is not None:
        background_tasks.add_task(run_self_heal, incident_id, service)
    else:  # no request context (e.g. the MCP tool) — run on a daemon thread
        import threading
        threading.Thread(target=run_self_heal, args=(incident_id, service), daemon=True).start()
    return "inproc"


def _enqueue_cloudtask(incident_id: str, service: str) -> None:
    from google.cloud import tasks_v2  # lazy import — only loaded in cloudtasks mode
    if not config.SELF_URL:
        raise RuntimeError("AIRBAG_SELF_URL must be set for cloudtasks mode")
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(config.GCP_PROJECT, config.CLOUD_TASKS_LOCATION,
                               config.CLOUD_TASKS_QUEUE)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{config.SELF_URL.rstrip('/')}/internal/run-heal",
            "headers": {"Content-Type": "application/json",
                        "x-airbag-internal-token": config.INTERNAL_TOKEN},
            "body": json.dumps({"incident_id": incident_id, "service": service}).encode(),
        },
        # >= worst-case run so a still-running heal isn't redelivered as 'failed' (idempotency is
        # the authoritative guard; this just avoids needless concurrent redelivery).
        "dispatch_deadline": {"seconds": int(config.HEAL_LEASE_S)},
    }
    client.create_task(request={"parent": parent, "task": task})
    log.info("enqueued Cloud Task for heal %s on %s", incident_id, service)

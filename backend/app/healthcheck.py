from __future__ import annotations

from typing import Any

from .delivery import delivery_service
from .jobs import job_manager
from .persistence import PostgresSnapshotStore
from .runtime import RuntimeSettings
from .tasking import tasking_service


def evaluate_runtime_health(settings: RuntimeSettings, *, tasking_preflight: bool = False) -> dict[str, Any]:
    state_store = {"provider": settings.state_store_mode, "ok": True}
    if settings.state_store_mode == "postgres":
        if settings.postgres_dsn:
            state_store = PostgresSnapshotStore(settings.postgres_dsn).health()
        else:
            state_store = {
                "provider": "postgres",
                "ok": False,
                "error": "Missing NEWERA_POSTGRES_DSN",
            }

    queue = job_manager.health()
    if settings.queue_provider != queue.get("provider"):
        queue["ok"] = False
        queue["error"] = (
            queue.get("error")
            or f"Configured queue provider '{settings.queue_provider}' but active provider is '{queue.get('provider')}'"
        )
    if settings.execution_mode == "queued" and not queue.get("ok", False):
        queue["required"] = True
    if settings.execution_mode == "queued" and not queue.get("worker_alive", False):
        queue["ok"] = False
        queue["error"] = queue.get("error") or "Worker is not alive"

    delivery = delivery_service.health(settings)
    tasking = tasking_service.health(settings, preflight=tasking_preflight)
    delivery_ok = bool(
        delivery.get("artifact_delivery", {}).get("ok", False)
        and delivery.get("notification", {}).get("ok", False)
    )
    tasking_ok = bool(tasking.get("ok", False))
    ready = bool(queue.get("ok", False) and state_store.get("ok", False) and delivery_ok and tasking_ok)
    return {
        "queue": queue,
        "state_store": state_store,
        "delivery": delivery,
        "tasking": tasking,
        "execution_mode": settings.execution_mode,
        "ready": ready,
    }

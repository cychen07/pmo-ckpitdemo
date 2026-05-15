from __future__ import annotations

import re
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .delivery import _http_json
from .runtime import RuntimeSettings, runtime_settings

ALERT_EVENT_PREFIXES: tuple[str, ...] = (
    "budget.warning",
    "budget.exhausted",
    "workitem.escalated",
    "agent.followup.failed",
    "job.dead_lettered",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertRouter:
    def __init__(self, buffer: int = 200) -> None:
        self._lock = threading.Lock()
        self._outbox: deque[dict[str, Any]] = deque(maxlen=buffer)
        self._seq = 0
        self._last_emitted_at: dict[str, float] = {}
        self._silenced_until: dict[str, float] = {}
        self._silence_cluster_keys: dict[str, str] = {}
        self._suppressed_counts: dict[str, int] = {}

    def should_route(self, event_name: str) -> bool:
        return any(event_name.startswith(prefix) for prefix in ALERT_EVENT_PREFIXES)

    def route_event(self, event: Any) -> None:
        if not self.should_route(getattr(event, "name", "")):
            return
        record = self._build_record(event)
        suppress_key = self._suppression_key(record)
        now = datetime.now(timezone.utc).timestamp()
        if self._silenced_until.get(suppress_key, 0) > now:
            self._suppressed_counts[suppress_key] = self._suppressed_counts.get(suppress_key, 0) + 1
            return
        dedup_window = max(float(runtime_settings.alert_dedup_window_sec), 0.0)
        if dedup_window > 0 and self._last_emitted_at.get(suppress_key, 0) > now - dedup_window:
            self._suppressed_counts[suppress_key] = self._suppressed_counts.get(suppress_key, 0) + 1
            return
        self._last_emitted_at[suppress_key] = now
        self._deliver_record(record)
        with self._lock:
            self._outbox.append(record)

    def resend(self, alert_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                record = next(item for item in self._outbox if item["id"] == alert_id)
            self._deliver_record(record)
            return dict(record)
        except StopIteration as exc:
            raise KeyError(alert_id) from exc

    def get_record(self, alert_id: str) -> dict[str, Any]:
        try:
            with self._lock:
                record = next(item for item in self._outbox if item["id"] == alert_id)
            return dict(record)
        except StopIteration as exc:
            raise KeyError(alert_id) from exc

    def resend_many(
        self,
        *,
        alert_ids: list[str] | None = None,
        status: str | None = None,
        cluster_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            candidates = list(self._outbox)
        if alert_ids:
            wanted = set(alert_ids)
            candidates = [item for item in candidates if item["id"] in wanted]
        elif cluster_key:
            candidates = [item for item in candidates if self._cluster_key(item) == cluster_key]
        elif status:
            candidates = [item for item in candidates if item.get("status") == status]
        else:
            candidates = [item for item in candidates if item.get("status") in {"failed", "buffered"}]
        resent: list[dict[str, Any]] = []
        for record in candidates:
            self._deliver_record(record)
            resent.append(dict(record))
        return resent

    def _deliver_record(self, record: dict[str, Any]) -> None:
        record["attempts"] = int(record.get("attempts", 0)) + 1
        record["error"] = None
        try:
            settings = runtime_settings
            if settings.notification_provider == "lark_webhook" and settings.lark_notify_enabled:
                _http_json(
                    settings.lark_bot_webhook_url or "",
                    method="POST",
                    payload={
                        "msg_type": "text",
                        "content": {
                            "text": self._render_record_message(record)
                        },
                    },
                )
                record["provider"] = "lark_webhook"
                record["status"] = "delivered"
                record["delivered_at"] = _now_iso()
            else:
                record["provider"] = "local"
                record["status"] = "buffered"
                record["delivered_at"] = None
        except Exception as exc:  # noqa: BLE001
            record["provider"] = "lark_webhook"
            record["status"] = "failed"
            record["error"] = str(exc)
            record["delivered_at"] = None

    def list_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._outbox)[-limit:][::-1]

    def list_cluster_records(self, cluster_key: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(item) for item in self._outbox if self._cluster_key(item) == cluster_key]
        return sorted(items, key=lambda item: item.get("timestamp", ""), reverse=True)

    def cluster_outbox(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        items = self.list_outbox(limit)
        if status:
            items = [item for item in items if item.get("status") == status]
        groups: dict[str, dict[str, Any]] = {}
        for item in items:
            key = self._cluster_key(item)
            group = groups.get(key)
            if group is None:
                group = {
                    "cluster_key": key,
                    "status": item.get("status"),
                    "event_name": item.get("event_name"),
                    "provider": item.get("provider"),
                    "reason": self._cluster_reason(item),
                    "severity": item.get("severity", "warning"),
                    "count": 0,
                    "alert_ids": [],
                    "last_timestamp": item.get("timestamp"),
                    "latest_summary": item.get("summary"),
                    "latest_error": item.get("error"),
                    "suppressed_count": self._suppressed_counts.get(self._suppression_key(item), 0),
                    "silenced_until": self._silence_deadline(self._suppression_key(item)),
                }
                groups[key] = group
            group["count"] += 1
            group["alert_ids"].append(item["id"])
            if item.get("timestamp", "") >= (group.get("last_timestamp") or ""):
                group["last_timestamp"] = item.get("timestamp")
                group["latest_summary"] = item.get("summary")
                group["latest_error"] = item.get("error")
            if item.get("severity") == "critical":
                group["severity"] = "critical"
        return sorted(groups.values(), key=lambda item: (-item["count"], item["cluster_key"]))

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        provider = active_settings.notification_provider
        if provider == "lark_webhook":
            ok = active_settings.lark_notify_enabled
            return {
                "provider": "lark_webhook",
                "ok": ok,
                "mode": "configured" if ok else "misconfigured",
                **({} if ok else {"error": "Missing NEWERA_LARK_BOT_WEBHOOK_URL"}),
            }
        return {
            "provider": "local",
            "ok": True,
            "mode": "buffered",
            "dedup_window_sec": active_settings.alert_dedup_window_sec,
            "silence_default_sec": active_settings.alert_silence_default_sec,
        }

    def stats(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        return {
            "dedup_window_sec": active_settings.alert_dedup_window_sec,
            "silence_default_sec": active_settings.alert_silence_default_sec,
            "suppressed_total": sum(self._suppressed_counts.values()),
            "silenced_clusters": sum(1 for value in self._silenced_until.values() if value > datetime.now(timezone.utc).timestamp()),
        }

    def list_silenced_clusters(self, limit: int = 50) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).timestamp()
        items: list[dict[str, Any]] = []
        for suppress_key, deadline in self._silenced_until.items():
            if deadline <= now:
                continue
            event_name, reason = self._parse_suppression_key(suppress_key)
            items.append(
                {
                    "cluster_key": self._silence_cluster_keys.get(
                        suppress_key,
                        f"{self._predicted_cluster_status()}::{suppress_key}",
                    ),
                    "event_name": event_name,
                    "reason": reason,
                    "provider": self._predicted_provider(),
                    "suppressed_count": self._suppressed_counts.get(suppress_key, 0),
                    "silenced_until": datetime.fromtimestamp(deadline, tz=timezone.utc).isoformat(),
                    "last_emitted_at": self._last_emitted_deadline(suppress_key),
                }
            )
        items.sort(key=lambda item: item["silenced_until"])
        return items[:limit]

    def silence_cluster(self, cluster_key: str, *, duration_sec: float | None = None) -> dict[str, Any]:
        window = max(float(duration_sec if duration_sec is not None else runtime_settings.alert_silence_default_sec), 0.0)
        deadline = datetime.now(timezone.utc).timestamp() + window
        suppress_key = self._suppression_key_from_cluster_key(cluster_key)
        self._silenced_until[suppress_key] = deadline
        self._silence_cluster_keys[suppress_key] = cluster_key
        return {
            "cluster_key": cluster_key,
            "silenced_until": datetime.fromtimestamp(deadline, tz=timezone.utc).isoformat(),
            "duration_sec": window,
        }

    def unsilence_cluster(self, cluster_key: str) -> dict[str, Any]:
        suppress_key = self._suppression_key_from_cluster_key(cluster_key)
        removed = self._silenced_until.pop(suppress_key, None)
        self._silence_cluster_keys.pop(suppress_key, None)
        return {
            "cluster_key": cluster_key,
            "cleared": removed is not None,
            "silenced_until": None,
        }

    def unsilence_many(self, cluster_keys: list[str]) -> list[dict[str, Any]]:
        return [self.unsilence_cluster(cluster_key) for cluster_key in cluster_keys]

    def reset(self) -> None:
        with self._lock:
            self._outbox.clear()
            self._seq = 0
        self._last_emitted_at.clear()
        self._silenced_until.clear()
        self._silence_cluster_keys.clear()
        self._suppressed_counts.clear()

    def _build_record(self, event: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            seq = self._seq
        return {
            "id": f"alert_out_{seq}",
            "event_name": getattr(event, "name", ""),
            "severity": self._severity(getattr(event, "name", "")),
            "summary": self._summary(event),
            "workitem_id": getattr(event, "workitem_id", None),
            "workflow_id": getattr(event, "workflow_id", None),
            "payload": getattr(event, "payload", {}),
            "timestamp": _now_iso(),
            "status": "pending",
            "provider": "local",
            "attempts": 0,
            "delivered_at": None,
            "error": None,
        }

    def _summary(self, event: Any) -> str:
        name = getattr(event, "name", "")
        workitem_id = getattr(event, "workitem_id", None) or "-"
        payload = getattr(event, "payload", {}) or {}
        if name == "job.dead_lettered":
            return f"Job dead-lettered on {workitem_id}: {payload.get('error', 'unknown error')}"
        if name.startswith("budget."):
            return f"Budget alert on {workitem_id}: {name}"
        if name == "workitem.escalated":
            return f"Workitem escalated: {workitem_id}"
        if name == "agent.followup.failed":
            return f"Agent follow-up failed on {workitem_id}: {payload.get('error', 'unknown error')}"
        return f"Alert event {name} on {workitem_id}"

    def _render_message(self, event: Any) -> str:
        return "[NewEra Alert]\n" + self._summary(event)

    def _render_record_message(self, record: dict[str, Any]) -> str:
        return "[NewEra Alert]\n" + str(record.get("summary", ""))

    def _severity(self, event_name: str) -> str:
        if event_name in {"budget.exhausted", "job.dead_lettered", "workitem.escalated"}:
            return "critical"
        return "warning"

    def _cluster_key(self, record: dict[str, Any]) -> str:
        reason = self._cluster_reason(record)
        return f"{record.get('status','unknown')}::{record.get('event_name','unknown')}::{reason}"

    def _cluster_reason(self, record: dict[str, Any]) -> str:
        if record.get("status") == "failed":
            error = str(record.get("error") or "delivery failed").lower()
            error = re.sub(r"\d+", "#", error)
            error = re.sub(r"\s+", " ", error).strip()
            return error[:80] or "delivery failed"
        if record.get("status") == "buffered":
            return "buffered locally"
        if record.get("status") == "delivered":
            return "delivered"
        return str(record.get("status") or "pending")

    def _suppression_key(self, record: dict[str, Any]) -> str:
        reason = self._cluster_reason(record)
        if record.get("status") == "pending":
            reason = self._predicted_cluster_reason()
        return f"{record.get('event_name','unknown')}::{reason}"

    def _suppression_key_from_cluster_key(self, cluster_key: str) -> str:
        parts = cluster_key.split("::", 1)
        return parts[1] if len(parts) == 2 else cluster_key

    def _predicted_cluster_reason(self) -> str:
        if runtime_settings.notification_provider == "local":
            return "buffered locally"
        if runtime_settings.notification_provider == "lark_webhook" and runtime_settings.lark_notify_enabled:
            return "delivered"
        return "delivery failed"

    def _predicted_cluster_status(self) -> str:
        if runtime_settings.notification_provider == "local":
            return "buffered"
        if runtime_settings.notification_provider == "lark_webhook" and runtime_settings.lark_notify_enabled:
            return "delivered"
        return "failed"

    def _predicted_provider(self) -> str:
        if runtime_settings.notification_provider == "lark_webhook":
            return "lark_webhook"
        return "local"

    def _silence_deadline(self, cluster_key: str) -> str | None:
        deadline = self._silenced_until.get(cluster_key)
        if not deadline:
            return None
        return datetime.fromtimestamp(deadline, tz=timezone.utc).isoformat()

    def _last_emitted_deadline(self, suppress_key: str) -> str | None:
        emitted_at = self._last_emitted_at.get(suppress_key)
        if not emitted_at:
            return None
        return datetime.fromtimestamp(emitted_at, tz=timezone.utc).isoformat()

    def _parse_suppression_key(self, suppress_key: str) -> tuple[str, str]:
        event_name, sep, reason = suppress_key.partition("::")
        if not sep:
            return suppress_key, "unknown"
        return event_name, reason


alert_router = AlertRouter()

from __future__ import annotations

import time
from typing import Any

from .delivery import _http_json
from .runtime import RuntimeSettings, runtime_settings


class LarkApiError(RuntimeError):
    def __init__(self, operation: str, payload: dict[str, Any]) -> None:
        self.operation = operation
        self.payload = payload
        self.code = payload.get("code")
        self.msg = payload.get("msg")
        super().__init__(f"{operation}: {payload}")


class LocalOpsTaskService:
    def create_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        return {
            "provider": "local",
            "delivery_status": "buffered",
            "external_ref": None,
        }

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        return {"provider": "local", "ok": True, "mode": "buffered"}

    def sync_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        return {
            "provider": "local",
            "status": task.get("status", "open"),
            "external_status": "buffered",
        }


class LarkWebhookOpsTaskService:
    def create_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        webhook = active_settings.lark_task_webhook_url or active_settings.lark_bot_webhook_url
        if not webhook:
            raise RuntimeError("Missing NEWERA_LARK_TASK_WEBHOOK_URL or NEWERA_LARK_BOT_WEBHOOK_URL")
        text = (
            "[NewEra Ops Task]\n"
            f"Title: {task['title']}\n"
            f"Severity: {task['severity']}\n"
            f"Workitem: {task['workitem_id']}\n"
            f"Summary: {task['summary']}"
        )
        _http_json(
            webhook,
            method="POST",
            payload={
                "msg_type": "text",
                "content": {"text": text},
            },
        )
        return {
            "provider": "lark_webhook",
            "delivery_status": "delivered",
            "external_ref": {
                "kind": "lark_webhook",
                "label": "Feishu Task Webhook",
                "url": webhook,
            },
        }

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        ok = active_settings.lark_task_enabled
        return {
            "provider": "lark_webhook",
            "ok": ok,
            "mode": "configured" if ok else "misconfigured",
            **({} if ok else {"error": "Missing NEWERA_LARK_TASK_WEBHOOK_URL or NEWERA_LARK_BOT_WEBHOOK_URL"}),
        }

    def sync_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        return {
            "provider": "lark_webhook",
            "status": task.get("status", "open"),
            "external_status": "delivered",
            "sync_note": "webhook provider does not support remote task state query",
        }


class LarkTaskApiService:
    def __init__(self) -> None:
        self._tenant_token: str | None = None
        self._token_expire_at: float = 0

    def _tenant_access_token(self, settings: RuntimeSettings) -> str:
        if self._tenant_token and self._token_expire_at > time.time() + 60:
            return self._tenant_token
        if not settings.lark_task_api_enabled:
            raise RuntimeError("Missing NEWERA_LARK_APP_ID/NEWERA_LARK_APP_SECRET")
        data = _http_json(
            f"{settings.lark_base_url}/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            payload={
                "app_id": settings.lark_app_id,
                "app_secret": settings.lark_app_secret,
            },
        )
        if data.get("code", 0) != 0:
            raise LarkApiError("Lark auth failed", data)
        self._tenant_token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._tenant_token

    def create_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        token = self._tenant_access_token(active_settings)
        payload: dict[str, Any] = {
            "summary": task["title"],
            "description": task["summary"][:3000],
            "client_token": task["id"],
            "origin": {
                "platform_i18n_name": {"zh_cn": "NewEra", "en_us": "NewEra"},
                "href": {
                    "title": task["title"],
                    "url": f"https://applink.feishu.cn/client/web_app/open?mode=appCenter",
                },
            },
            "extra": task["workitem_id"],
        }
        if active_settings.lark_tasklist_guid:
            payload["tasklists"] = [{"tasklist_guid": active_settings.lark_tasklist_guid}]
        created = _http_json(
            f"{active_settings.lark_base_url}/open-apis/task/v2/tasks",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
            payload=payload,
        )
        if created.get("code", 0) != 0:
            raise RuntimeError(f"Lark task create failed: {created}")
        task_data = created.get("data", {}).get("task", {})
        return {
            "provider": "lark_task_api",
            "delivery_status": "delivered",
            "external_ref": {
                "kind": "lark_task",
                "label": "Feishu Task",
                "url": task_data.get("url") or "",
            },
            "guid": task_data.get("guid"),
            "task_url": task_data.get("url"),
        }

    def health(self, settings: RuntimeSettings | None = None, *, preflight: bool = False) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        ok = active_settings.lark_task_api_enabled
        result: dict[str, Any] = {
            "provider": "lark_task_api",
            "ok": ok,
            "mode": "configured" if ok else "misconfigured",
            "tasklist_guid_configured": bool(active_settings.lark_tasklist_guid),
            **({} if ok else {"error": "Missing NEWERA_LARK_APP_ID/NEWERA_LARK_APP_SECRET"}),
        }
        if not ok or not preflight:
            return result
        try:
            self._tenant_access_token(active_settings)
            result["preflight"] = "passed"
            return result
        except LarkApiError as exc:
            result.update(
                {
                    "ok": False,
                    "mode": "auth_failed",
                    "preflight": "failed",
                    "error": str(exc),
                    "lark_error_code": exc.code,
                    "lark_error_msg": exc.msg,
                }
            )
            return result
        except Exception as exc:  # noqa: BLE001
            result.update(
                {
                    "ok": False,
                    "mode": "auth_failed",
                    "preflight": "failed",
                    "error": str(exc),
                }
            )
            return result

    def sync_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        guid = task.get("external_task_guid")
        if not guid:
            raise RuntimeError("Missing external_task_guid for lark task sync")
        token = self._tenant_access_token(active_settings)
        payload = _http_json(
            f"{active_settings.lark_base_url}/open-apis/task/v2/tasks/{guid}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        if payload.get("code", 0) != 0:
            raise RuntimeError(f"Lark task get failed: {payload}")
        task_data = payload.get("data", {}).get("task", {})
        external_status = str(task_data.get("status") or "todo")
        return {
            "provider": "lark_task_api",
            "status": "completed" if external_status == "done" else "open",
            "external_status": external_status,
            "completed_at": task_data.get("completed_at"),
            "external_ref": {
                "kind": "lark_task",
                "label": "Feishu Task",
                "url": task_data.get("url") or (task.get("external_ref") or {}).get("url") or "",
            },
            "external_task_guid": task_data.get("guid") or guid,
        }


class TaskingService:
    def __init__(self) -> None:
        self.local = LocalOpsTaskService()
        self.lark = LarkWebhookOpsTaskService()
        self.lark_api = LarkTaskApiService()

    def create_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        if active_settings.ops_task_provider == "lark_task_api":
            try:
                return self.lark_api.create_task(task, active_settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "provider": "lark_task_api",
                    "delivery_status": "failed",
                    "external_ref": None,
                    "error": str(exc),
                }
        if active_settings.ops_task_provider == "lark_webhook":
            try:
                return self.lark.create_task(task, active_settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "provider": "lark_webhook",
                    "delivery_status": "failed",
                    "external_ref": None,
                    "error": str(exc),
                }
        return self.local.create_task(task, active_settings)

    def health(self, settings: RuntimeSettings | None = None, *, preflight: bool = False) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        if active_settings.ops_task_provider == "lark_task_api":
            return self.lark_api.health(active_settings, preflight=preflight)
        if active_settings.ops_task_provider == "lark_webhook":
            return self.lark.health(active_settings)
        return self.local.health(active_settings)

    def sync_task(self, task: dict[str, Any], settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        provider = task.get("provider") or active_settings.ops_task_provider
        try:
            if provider == "lark_task_api":
                return self.lark_api.sync_task(task, active_settings)
            if provider == "lark_webhook":
                return self.lark.sync_task(task, active_settings)
            return self.local.sync_task(task, active_settings)
        except Exception as exc:  # noqa: BLE001
            return {
                "provider": provider,
                "status": task.get("status", "open"),
                "external_status": task.get("external_status", "unknown"),
                "sync_error": str(exc),
            }


tasking_service = TaskingService()

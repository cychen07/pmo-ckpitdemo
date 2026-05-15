from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .artifact_store import artifact_store
from .domain.models import Artifact, Workitem
from .runtime import RuntimeSettings, runtime_settings


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    final_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        final_headers = {
            "Content-Type": "application/json; charset=utf-8",
            **final_headers,
        }
    req = request.Request(url, data=body, method=method, headers=final_headers)
    try:
        with request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:  # pragma: no cover - network dependency
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"{method} {url} failed: {exc.code} {detail}") from exc


class LocalDeliveryService:
    def publish_artifact(
        self,
        artifact: Artifact,
        workitem: Workitem,
        content: str,
        content_type: str,
    ) -> list[dict[str, str]]:
        return [
            {
                "kind": "local_artifact",
                "label": "Local Artifact",
                "url": artifact.uri,
            }
        ]

    def notify(self, workitem: Workitem, artifact: Artifact, summary: str) -> None:
        return None

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        return {"provider": "local", "ok": True, "mode": "local"}


class LarkDeliveryService:
    def __init__(self) -> None:
        self._tenant_token: str | None = None
        self._token_expire_at: float = 0

    def _tenant_access_token(self) -> str:
        if self._tenant_token and self._token_expire_at > time.time() + 60:
            return self._tenant_token
        if not runtime_settings.lark_doc_enabled:
            raise RuntimeError("Lark doc integration is not configured")
        data = _http_json(
            f"{runtime_settings.lark_base_url}/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            payload={
                "app_id": runtime_settings.lark_app_id,
                "app_secret": runtime_settings.lark_app_secret,
            },
        )
        if data.get("code", 0) != 0:
            raise RuntimeError(f"Lark auth failed: {data}")
        self._tenant_token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._tenant_token

    def publish_artifact(
        self,
        artifact: Artifact,
        workitem: Workitem,
        content: str,
        content_type: str,
    ) -> list[dict[str, str]]:
        token = self._tenant_access_token()
        created = _http_json(
            f"{runtime_settings.lark_base_url}/open-apis/docx/v1/documents",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
            payload={
                "title": artifact.title,
                **(
                    {"folder_token": runtime_settings.lark_doc_folder_token}
                    if runtime_settings.lark_doc_folder_token
                    else {}
                ),
            },
        )
        if created.get("code", 0) != 0:
            raise RuntimeError(f"Lark create doc failed: {created}")
        document_id = created["data"]["document"]["document_id"]
        update_payload = {
            "update_text_elements": {
                "elements": [
                    {
                        "text_run": {
                            "content": content[:20000],
                        }
                    }
                ]
            }
        }
        updated = _http_json(
            (
                f"{runtime_settings.lark_base_url}/open-apis/docx/v1/documents/"
                f"{document_id}/blocks/{document_id}?document_revision_id=-1"
            ),
            method="PATCH",
            headers={"Authorization": f"Bearer {token}"},
            payload=update_payload,
        )
        if updated.get("code", 0) != 0:
            raise RuntimeError(f"Lark update doc failed: {updated}")
        return [
            {
                "kind": "lark_doc",
                "label": "Feishu Doc",
                "url": f"{runtime_settings.lark_base_url.replace('open.', '')}/docx/{document_id}",
            }
        ]

    def notify(self, workitem: Workitem, artifact: Artifact, summary: str) -> None:
        webhook = runtime_settings.lark_bot_webhook_url
        if not webhook:
            return
        _http_json(
            webhook,
            method="POST",
            payload={
                "msg_type": "text",
                "content": {
                    "text": (
                        f"[NewEra] {workitem.title} 已产出 {artifact.title}\n"
                        f"状态: {workitem.state.value}\n"
                        f"摘要: {summary}"
                    )
                },
            },
        )

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        issues: list[str] = []
        if not active_settings.lark_doc_enabled:
            issues.append("Missing NEWERA_LARK_APP_ID/NEWERA_LARK_APP_SECRET")
        return {
            "provider": "lark",
            "ok": not issues,
            "mode": "configured" if not issues else "misconfigured",
            **({"error": "; ".join(issues)} if issues else {}),
            "base_url": active_settings.lark_base_url,
            "folder_token_configured": bool(active_settings.lark_doc_folder_token),
        }


class DeliveryService:
    def __init__(self) -> None:
        self.local = LocalDeliveryService()
        self.lark = LarkDeliveryService()

    def publish_artifact(
        self,
        artifact: Artifact,
        workitem: Workitem,
    ) -> list[dict[str, str]]:
        stored = artifact_store.read(artifact.id)
        if runtime_settings.delivery_provider == "lark" and runtime_settings.lark_doc_enabled:
            try:
                refs = self.lark.publish_artifact(
                    artifact,
                    workitem,
                    stored["content"],
                    stored["content_type"],
                )
            except Exception:
                refs = self.local.publish_artifact(
                    artifact,
                    workitem,
                    stored["content"],
                    stored["content_type"],
                )
        else:
            refs = self.local.publish_artifact(
                artifact,
                workitem,
                stored["content"],
                stored["content_type"],
            )
        artifact.external_refs.extend(refs)
        return refs

    def notify(self, workitem: Workitem, artifact: Artifact, summary: str) -> None:
        if runtime_settings.notification_provider == "lark_webhook" and runtime_settings.lark_notify_enabled:
            try:
                self.lark.notify(workitem, artifact, summary)
                return
            except Exception:
                return
        self.local.notify(workitem, artifact, summary)

    def health(self, settings: RuntimeSettings | None = None) -> dict[str, Any]:
        active_settings = settings or runtime_settings
        artifact_delivery = (
            self.lark.health(active_settings)
            if active_settings.delivery_provider == "lark"
            else self.local.health(active_settings)
        )
        notification = {
            "provider": active_settings.notification_provider,
            "ok": True,
            "mode": "local",
        }
        if active_settings.notification_provider == "lark_webhook":
            notification = {
                "provider": "lark_webhook",
                "ok": active_settings.lark_notify_enabled,
                "mode": "configured" if active_settings.lark_notify_enabled else "misconfigured",
                **(
                    {}
                    if active_settings.lark_notify_enabled
                    else {"error": "Missing NEWERA_LARK_BOT_WEBHOOK_URL"}
                ),
            }
        return {
            "artifact_delivery": artifact_delivery,
            "notification": notification,
        }


delivery_service = DeliveryService()

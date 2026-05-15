from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RuntimeSettings:
    delivery_provider: str = "local"  # local | lark
    notification_provider: str = "local"  # local | lark_webhook
    ops_task_provider: str = "local"  # local | lark_webhook | lark_task_api
    execution_mode: str = "inline"  # inline | queued
    state_store_mode: str = "json_snapshot"  # json_snapshot | postgres
    object_store_mode: str = "localfs"  # localfs | s3
    queue_provider: str = "memory"  # memory | redis
    redis_url: str | None = None
    postgres_dsn: str | None = None
    snapshot_key: str = "default"
    startup_strict: bool = False
    job_timeout_sec: float = 8.0
    job_retry_backoff_sec: float = 0.25
    dead_letter_enabled: bool = True
    lark_base_url: str = "https://open.feishu.cn"
    lark_doc_folder_token: str | None = None
    lark_app_id: str | None = None
    lark_app_secret: str | None = None
    lark_bot_webhook_url: str | None = None
    lark_task_webhook_url: str | None = None
    lark_tasklist_guid: str | None = None
    alert_dedup_window_sec: float = 60.0
    alert_silence_default_sec: float = 600.0

    @property
    def lark_doc_enabled(self) -> bool:
        return bool(self.lark_app_id and self.lark_app_secret)

    @property
    def lark_notify_enabled(self) -> bool:
        return bool(self.lark_bot_webhook_url)

    @property
    def lark_task_enabled(self) -> bool:
        return bool(self.lark_task_webhook_url or self.lark_bot_webhook_url)

    @property
    def lark_task_api_enabled(self) -> bool:
        return bool(self.lark_app_id and self.lark_app_secret)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["lark_doc_enabled"] = self.lark_doc_enabled
        data["lark_notify_enabled"] = self.lark_notify_enabled
        data["lark_task_enabled"] = self.lark_task_enabled
        data["lark_task_api_enabled"] = self.lark_task_api_enabled
        # 不把 secret 原样回显到前端
        if data["lark_app_secret"]:
            data["lark_app_secret"] = "***"
        if data["lark_task_webhook_url"]:
            data["lark_task_webhook_url"] = "***"
        if data["lark_bot_webhook_url"]:
            data["lark_bot_webhook_url"] = "***"
        return data


def load_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings(
        delivery_provider=os.environ.get("NEWERA_DELIVERY_PROVIDER", "local"),
        notification_provider=os.environ.get("NEWERA_NOTIFICATION_PROVIDER", "local"),
        ops_task_provider=os.environ.get("NEWERA_OPS_TASK_PROVIDER", "local"),
        execution_mode=os.environ.get("NEWERA_EXECUTION_MODE", "inline"),
        state_store_mode=os.environ.get("NEWERA_STATE_STORE_MODE", "json_snapshot"),
        object_store_mode=os.environ.get("NEWERA_OBJECT_STORE_MODE", "localfs"),
        queue_provider=os.environ.get("NEWERA_QUEUE_PROVIDER", "memory"),
        redis_url=os.environ.get("NEWERA_REDIS_URL"),
        postgres_dsn=os.environ.get("NEWERA_POSTGRES_DSN"),
        snapshot_key=os.environ.get("NEWERA_SNAPSHOT_KEY", "default"),
        startup_strict=os.environ.get("NEWERA_STARTUP_STRICT", "false").lower() == "true",
        job_timeout_sec=float(os.environ.get("NEWERA_JOB_TIMEOUT_SEC", "8")),
        job_retry_backoff_sec=float(os.environ.get("NEWERA_JOB_RETRY_BACKOFF_SEC", "0.25")),
        dead_letter_enabled=os.environ.get("NEWERA_DEAD_LETTER_ENABLED", "true").lower() == "true",
        lark_base_url=os.environ.get("NEWERA_LARK_BASE_URL", "https://open.feishu.cn"),
        lark_doc_folder_token=os.environ.get("NEWERA_LARK_DOC_FOLDER_TOKEN"),
        lark_app_id=os.environ.get("NEWERA_LARK_APP_ID"),
        lark_app_secret=os.environ.get("NEWERA_LARK_APP_SECRET"),
        lark_bot_webhook_url=os.environ.get("NEWERA_LARK_BOT_WEBHOOK_URL"),
        lark_task_webhook_url=os.environ.get("NEWERA_LARK_TASK_WEBHOOK_URL"),
        lark_tasklist_guid=os.environ.get("NEWERA_LARK_TASKLIST_GUID"),
        alert_dedup_window_sec=float(os.environ.get("NEWERA_ALERT_DEDUP_WINDOW_SEC", "60")),
        alert_silence_default_sec=float(os.environ.get("NEWERA_ALERT_SILENCE_DEFAULT_SEC", "600")),
    )


runtime_settings = load_runtime_settings()

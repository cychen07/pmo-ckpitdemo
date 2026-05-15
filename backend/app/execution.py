from __future__ import annotations

from typing import Any

from .jobs import Job, job_manager
from .repository import InMemoryStore
from .runtime import runtime_settings


class ExecutionGateway:
    """执行网关。

    当前默认 inline，同步调用 store.run_agent。
    后续切换 queued / worker 时，只需要替换这里，不需要改 API 路由。
    """

    def run_agent(
        self,
        store: InMemoryStore,
        workitem_id: str,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if runtime_settings.execution_mode == "queued":
            job = job_manager.submit_run_agent(
                workitem_id=workitem_id,
                actor=actor,
                payload=payload,
                runner=lambda queued_job: self._run_inline_from_job(store, queued_job),
            )
            return {
                "mode": "queued",
                "job": job,
                "runtime": self.runtime_info(),
            }
        result = store.run_agent(workitem_id, actor, payload)
        result["mode"] = "inline"
        result["runtime"] = self.runtime_info()
        return result

    def runtime_info(self) -> dict[str, Any]:
        return {
            "execution_mode": runtime_settings.execution_mode,
            "state_store_mode": runtime_settings.state_store_mode,
            "object_store_mode": runtime_settings.object_store_mode,
            "job_stats": job_manager.stats(),
        }

    def _run_inline_from_job(self, store: InMemoryStore, job: Job) -> dict[str, Any]:
        result = store.run_agent(job.workitem_id, job.actor, job.payload)
        store._persist()  # noqa: SLF001
        result["mode"] = "queued"
        result["runtime"] = self.runtime_info()
        return result


execution_gateway = ExecutionGateway()

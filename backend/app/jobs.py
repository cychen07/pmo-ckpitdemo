from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .domain.models import new_id, now_iso
from .events import bus
from .runtime import runtime_settings

try:  # pragma: no cover - optional dependency
    import queue
except Exception:  # noqa: BLE001
    queue = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    import redis
except Exception:  # noqa: BLE001
    redis = None  # type: ignore[assignment]


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str
    workitem_id: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    last_failure_kind: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    timeout_sec: float = 8.0
    retry_backoff_sec: float = 0.25
    source_job_id: str | None = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    next_retry_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "workitem_id": self.workitem_id,
            "actor": self.actor,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "last_failure_kind": self.last_failure_kind,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "timeout_sec": self.timeout_sec,
            "retry_backoff_sec": self.retry_backoff_sec,
            "source_job_id": self.source_job_id,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "next_retry_at": self.next_retry_at,
        }


class MemoryQueueBackend:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()

    def enqueue(self, job_id: str) -> None:
        self._queue.put(job_id)

    def dequeue(self, timeout: float = 0.25) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except Exception:
            return None

    def ack(self) -> None:
        try:
            self._queue.task_done()
        except Exception:
            pass

    def reset(self) -> None:
        self._queue = queue.Queue()

    def health(self) -> dict[str, Any]:
        return {"provider": "memory", "ok": True}


class RedisQueueBackend:
    def __init__(self, redis_url: str) -> None:
        if redis is None:
            raise RuntimeError("redis package is required for redis queue provider")
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)  # type: ignore[union-attr]
        self._key = "newera:jobs:queue"

    def enqueue(self, job_id: str) -> None:
        self._client.lpush(self._key, job_id)

    def dequeue(self, timeout: float = 0.25) -> str | None:
        item = self._client.brpop(self._key, timeout=max(1, int(timeout)))
        if not item:
            return None
        _, job_id = item
        return str(job_id)

    def ack(self) -> None:
        return None

    def reset(self) -> None:
        try:
            self._client.delete(self._key)
        except Exception:
            pass

    def health(self) -> dict[str, Any]:
        try:
            pong = self._client.ping()
            return {"provider": "redis", "ok": bool(pong)}
        except Exception as exc:
            return {"provider": "redis", "ok": False, "error": str(exc)}


class JobManager:
    def __init__(self) -> None:
        self._backend: MemoryQueueBackend | RedisQueueBackend = MemoryQueueBackend()
        self._jobs: dict[str, Job] = {}
        self._runners: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._queue_provider = "memory"

    def configure(self) -> None:
        provider = runtime_settings.queue_provider
        if provider == "redis" and runtime_settings.redis_url:
            try:
                self._backend = RedisQueueBackend(runtime_settings.redis_url)
                self._queue_provider = "redis"
                return
            except Exception:
                self._backend = MemoryQueueBackend()
        self._backend = MemoryQueueBackend()
        self._queue_provider = "memory"

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self.configure()
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, daemon=True, name="newera-job-worker")
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._backend.enqueue("__stop__")
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.5)

    def reset(self) -> None:
        self.stop()
        with self._lock:
            self._jobs.clear()
            self._runners.clear()
        self._backend.reset()
        self._worker = None
        self._stop = threading.Event()

    def submit_run_agent(
        self,
        *,
        workitem_id: str,
        actor: str,
        payload: dict[str, Any],
        runner: Any,
    ) -> dict[str, Any]:
        max_attempts = int(payload.get("__job_max_attempts", 3) or 3)
        timeout_sec = float(payload.get("__job_timeout_sec", runtime_settings.job_timeout_sec) or runtime_settings.job_timeout_sec)
        retry_backoff_sec = float(
            payload.get("__job_retry_backoff_sec", runtime_settings.job_retry_backoff_sec)
            or runtime_settings.job_retry_backoff_sec
        )
        job = Job(
            id=new_id("job"),
            kind="run_agent",
            status="queued",
            workitem_id=workitem_id,
            actor=actor,
            payload=payload,
            attempts=0,
            max_attempts=max_attempts,
            timeout_sec=timeout_sec,
            retry_backoff_sec=retry_backoff_sec,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._runners[job.id] = runner
        self._backend.enqueue(job.id)
        bus.publish(
            "job.queued",
            workitem_id=workitem_id,
            payload={"job_id": job.id, "kind": job.kind},
        )
        return job.to_dict()

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in {"failed", "cancelled", "dead_lettered"}:
                raise ValueError("Only failed/cancelled/dead-letter jobs can be retried")
            job.status = "queued"
            job.error = None
            job.last_failure_kind = None
            job.result = None
            job.cancel_requested = False
            job.started_at = None
            job.finished_at = None
            job.attempts = 0
            job.next_retry_at = None
        self._backend.enqueue(job_id)
        bus.publish(
            "job.retried",
            workitem_id=job.workitem_id,
            payload={"job_id": job.id, "attempts": job.attempts},
        )
        return self.get_job(job_id)

    def replay_job(
        self,
        job_id: str,
        *,
        payload_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            source = self._jobs[job_id]
            runner = self._runners[job_id]
        merged_payload = dict(source.payload)
        if payload_override:
            merged_payload.update(payload_override)
        replayed = self.submit_run_agent(
            workitem_id=source.workitem_id,
            actor=source.actor,
            payload=merged_payload,
            runner=runner,
        )
        with self._lock:
            self._jobs[replayed["id"]].source_job_id = source.id
        bus.publish(
            "job.replayed",
            workitem_id=source.workitem_id,
            payload={
                "source_job_id": source.id,
                "job_id": replayed["id"],
            },
        )
        return self.get_job(replayed["id"])

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = now_iso()
            elif job.status == "running":
                job.cancel_requested = True
                job.status = "cancel_requested"
            elif job.status in {"succeeded", "failed", "cancelled"}:
                raise ValueError("Job is already terminal")
            else:
                raise ValueError(f"Cannot cancel job in status {job.status}")
        bus.publish(
            "job.cancelled" if job.status == "cancelled" else "job.cancel_requested",
            workitem_id=job.workitem_id,
            payload={"job_id": job.id, "kind": job.kind},
        )
        return self.get_job(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)]

    def list_dead_letters(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j.to_dict() for j in self._jobs.values() if j.status == "dead_lettered"]
        return sorted(jobs, key=lambda x: x["created_at"], reverse=True)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            return job.to_dict()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            jobs = list(self._jobs.values())
        return {
            "provider": self._queue_provider,
            "queued": sum(1 for j in jobs if j.status == "queued"),
            "running": sum(1 for j in jobs if j.status == "running"),
            "succeeded": sum(1 for j in jobs if j.status == "succeeded"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
            "dead_lettered": sum(1 for j in jobs if j.status == "dead_lettered"),
            "cancel_requested": sum(1 for j in jobs if j.status == "cancel_requested"),
            "cancelled": sum(1 for j in jobs if j.status == "cancelled"),
            "total": len(jobs),
            "worker_alive": bool(self._worker and self._worker.is_alive()),
        }

    def health(self) -> dict[str, Any]:
        data = self._backend.health()
        data["worker_alive"] = bool(self._worker and self._worker.is_alive())
        data["provider"] = self._queue_provider
        return data

    def _run(self) -> None:
        while not self._stop.is_set():
            job_id = self._backend.dequeue()
            if not job_id:
                continue
            if job_id == "__stop__":
                self._backend.ack()
                break
            with self._lock:
                current = self._jobs.get(job_id)
                runner = self._runners.get(job_id)
                if current is None or runner is None:
                    self._backend.ack()
                    continue
                if current.status == "cancelled":
                    self._backend.ack()
                    continue
                current.attempts += 1
                current.status = "running"
                current.started_at = now_iso()
                current.next_retry_at = None
            bus.publish(
                "job.started",
                workitem_id=current.workitem_id,
                payload={"job_id": current.id, "kind": current.kind, "attempts": current.attempts},
            )
            try:
                result = self._run_with_timeout(current, runner)
                with self._lock:
                    current = self._jobs[job_id]
                    if current.cancel_requested:
                        current.status = "cancelled"
                        current.error = "Job was cancelled after execution completed"
                    else:
                        current.status = "succeeded"
                    current.result = result
                    current.finished_at = now_iso()
                bus.publish(
                    "job.cancelled" if current.cancel_requested else "job.succeeded",
                    workitem_id=current.workitem_id,
                    payload={"job_id": current.id, "kind": current.kind},
                )
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    current = self._jobs[job_id]
                    if current.cancel_requested:
                        current.status = "cancelled"
                        current.error = "Job was cancelled while running"
                    else:
                        current.last_failure_kind = "timeout" if isinstance(exc, TimeoutError) else "error"
                        current.error = str(exc)
                        current.finished_at = now_iso()
                        if current.attempts < current.max_attempts:
                            current.status = "queued"
                            current.next_retry_at = self._schedule_retry(current.id, current.retry_backoff_sec)
                        else:
                            current.next_retry_at = None
                            current.status = "dead_lettered" if runtime_settings.dead_letter_enabled else "failed"
                if current.cancel_requested:
                    event_name = "job.cancelled"
                elif current.status == "queued":
                    event_name = "job.retry_scheduled"
                elif current.status == "dead_lettered":
                    event_name = "job.dead_lettered"
                else:
                    event_name = "job.failed"
                bus.publish(
                    event_name,
                    workitem_id=current.workitem_id,
                    payload={
                        "job_id": current.id,
                        "kind": current.kind,
                        "error": current.error,
                        "attempts": current.attempts,
                        "max_attempts": current.max_attempts,
                        "last_failure_kind": current.last_failure_kind,
                        "next_retry_at": current.next_retry_at,
                    },
                )
            finally:
                self._backend.ack()

    def _schedule_retry(self, job_id: str, delay_sec: float) -> str:
        next_retry_at = _future_iso(delay_sec)
        timer = threading.Timer(
            delay_sec,
            lambda: self._backend.enqueue(job_id),
        )
        timer.daemon = True
        timer.start()
        return next_retry_at

    def _run_with_timeout(self, job: Job, runner: Any) -> dict[str, Any]:
        if job.timeout_sec <= 0:
            return runner(job)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"newera-job-{job.id}",
        )
        future = executor.submit(runner, job)
        try:
            return future.result(timeout=job.timeout_sec)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Job {job.id} timed out after {job.timeout_sec:.2f}s") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


job_manager = JobManager()


def _future_iso(delay_sec: float) -> str:
    target = datetime.fromtimestamp(time.time() + max(0.0, delay_sec), tz=timezone.utc)
    return target.isoformat().replace("+00:00", "Z")

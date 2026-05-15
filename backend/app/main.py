from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .artifact_store import artifact_store
from .alerting import alert_router
from .delivery import delivery_service
from .execution import execution_gateway
from .healthcheck import evaluate_runtime_health
from .jobs import job_manager
from .auth import ROLE_PERMISSIONS, User, get_current_user, require_permission
from .domain.state_machine import StateTransitionError
from .events import bus
from .metrics import aggregate_metrics, list_alerts, workflow_overview
from .repository import IdempotencyConflict, store, to_dict
from .runtime import runtime_settings
from .tasking import tasking_service


class ActionPayload(BaseModel):
    actor: str | None = None
    payload: dict[str, Any] = {}


class RecommendationPayload(BaseModel):
    capability: str | None = None


class TemplatePayload(BaseModel):
    id: str | None = None
    title: str
    description: str = ""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    sla: str = "P3D"
    rollback_policy: str = "human_takeover"
    owner: str = "u_yang"


class TemplatePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    sla: str | None = None
    rollback_policy: str | None = None
    owner: str | None = None
    edges: list[dict[str, str]] | None = None


class TemplateInstantiatePayload(BaseModel):
    title: str | None = None


class WorkflowCreatePayload(BaseModel):
    title: str
    template_id: str | None = None
    owner: str | None = None
    sla: str = "P3D"
    rollback_policy: str = "human_takeover"


class JobReplayPayload(BaseModel):
    payload: dict[str, Any] = {}


class AlertBatchResendPayload(BaseModel):
    alert_ids: list[str] = []
    status: str | None = None
    cluster_key: str | None = None


class OpsTaskCreatePayload(BaseModel):
    title: str | None = None
    summary: str | None = None
    severity: str = "critical"


class AlertSilencePayload(BaseModel):
    cluster_key: str
    duration_sec: float | None = None


class AlertSilenceBatchPayload(BaseModel):
    cluster_keys: list[str] = []


SNAPSHOT_PATH = os.environ.get("NEWERA_SNAPSHOT", "./data/store.json")
ARTIFACT_DIR = os.environ.get("NEWERA_ARTIFACT_DIR", "./data/artifacts")


def _pick_alert_workitem_id(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        workitem_id = record.get("workitem_id")
        if isinstance(workitem_id, str) and workitem_id in store.workitems:
            return workitem_id
    return None


def _alert_op_severity(records: list[dict[str, Any]], default: str = "warning") -> str:
    if any(record.get("severity") == "critical" for record in records):
        return "critical"
    return default


def _maybe_create_alert_ops_task(
    *,
    actor: str,
    title: str,
    summary: str,
    source_kind: str,
    source_ref: str,
    records: list[dict[str, Any]],
    severity: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    workitem_id = _pick_alert_workitem_id(records)
    if not workitem_id:
        return None
    return store.create_ops_task(
        workitem_id=workitem_id,
        actor=actor,
        title=title,
        summary=summary,
        severity=severity or _alert_op_severity(records),
        source_kind=source_kind,
        source_ref=source_ref,
        metadata=metadata,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.configure_persistence(
        SNAPSHOT_PATH,
        mode=runtime_settings.state_store_mode,
        postgres_dsn=runtime_settings.postgres_dsn,
        snapshot_key=runtime_settings.snapshot_key,
    )
    artifact_store.configure(ARTIFACT_DIR)
    store.load_snapshot()
    job_manager.start()
    health = evaluate_runtime_health(runtime_settings)
    app.state.startup_health = health
    if runtime_settings.startup_strict and not health["ready"]:
        raise RuntimeError(f"Startup self-check failed: {health}")
    yield
    job_manager.stop()


app = FastAPI(title="NewEra Command Deck API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    health = evaluate_runtime_health(runtime_settings, tasking_preflight=True)
    app.state.startup_health = health
    status_code = 200 if health["ready"] else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if health["ready"] else "degraded",
            **health,
        },
    )


# ---------------------------------------------------------------------------
# AUTH-01: whoami
# ---------------------------------------------------------------------------
@app.get("/v1/whoami")
def whoami(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
    }


@app.get("/v1/runtime")
def get_runtime_status(user: User = Depends(require_permission("read"))) -> dict[str, Any]:
    data = runtime_settings.to_dict()
    data["job_stats"] = job_manager.stats()
    data["delivery_health"] = delivery_service.health(runtime_settings)
    data["alerting_health"] = alert_router.health(runtime_settings)
    data["alerting_stats"] = alert_router.stats(runtime_settings)
    data["tasking_health"] = tasking_service.health(runtime_settings)
    return data


@app.get("/v1/runtime/health")
def get_runtime_health(user: User = Depends(require_permission("read"))) -> dict[str, Any]:
    return evaluate_runtime_health(runtime_settings)


@app.post("/v1/workflows")
def create_workflow(
    body: WorkflowCreatePayload,
    user: User = Depends(require_permission("workflow.start")),
) -> dict[str, Any]:
    return store.create_workflow(
        title=body.title,
        template_id=body.template_id,
        owner=body.owner or user.id,
        sla=body.sla,
        rollback_policy=body.rollback_policy,
    )


@app.get("/v1/workflows")
def list_workflows(user: User = Depends(require_permission("read"))) -> list[dict[str, Any]]:
    return store.list_workflows()


@app.get("/v1/workflows/{workflow_id}")
def get_workflow(
    workflow_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    if workflow_id not in store.workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return store.workflow_detail(workflow_id)


@app.get("/v1/workitems/{workitem_id}")
def get_workitem(
    workitem_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    if workitem_id not in store.workitems:
        raise HTTPException(status_code=404, detail="Workitem not found")
    return to_dict(store.workitems[workitem_id])


@app.post("/v1/workitems/{workitem_id}/assign")
def assign(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("assign")),
) -> dict[str, Any]:
    return _apply(workitem_id, "assign", body, user)


@app.post("/v1/workitems/{workitem_id}/start")
def start(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("start")),
) -> dict[str, Any]:
    return _apply(workitem_id, "start", body, user)


@app.post("/v1/workitems/{workitem_id}/pause")
def pause(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("pause")),
) -> dict[str, Any]:
    return _apply(workitem_id, "pause", body, user)


@app.post("/v1/workitems/{workitem_id}/resume")
def resume(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("resume")),
) -> dict[str, Any]:
    return _apply(workitem_id, "resume", body, user)


@app.post("/v1/workitems/{workitem_id}/takeover")
def takeover(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("takeover")),
) -> dict[str, Any]:
    return _apply(workitem_id, "takeover", body, user)


@app.post("/v1/workitems/{workitem_id}/request_decision")
def request_decision(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("request_decision")),
) -> dict[str, Any]:
    return _apply(workitem_id, "request_decision", body, user)


@app.post("/v1/workitems/{workitem_id}/decide")
def decide(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("decide")),
) -> dict[str, Any]:
    return _apply(workitem_id, "decide", body, user)


@app.post("/v1/workitems/{workitem_id}/submit")
def submit(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("submit")),
) -> dict[str, Any]:
    return _apply(workitem_id, "submit", body, user)


@app.post("/v1/workitems/{workitem_id}/approve")
def approve(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("approve")),
) -> dict[str, Any]:
    return _apply(workitem_id, "approve", body, user)


@app.post("/v1/workitems/{workitem_id}/reject")
def reject(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("reject")),
) -> dict[str, Any]:
    return _apply(workitem_id, "reject", body, user)


@app.post("/v1/workitems/{workitem_id}/escalate")
def escalate(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("escalate")),
) -> dict[str, Any]:
    return _apply(workitem_id, "escalate", body, user)


@app.post("/v1/workitems/{workitem_id}/cancel")
def cancel(
    workitem_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("cancel")),
) -> dict[str, Any]:
    return _apply(workitem_id, "cancel", body, user)


@app.get("/v1/workitems/{workitem_id}/trace")
def get_trace(
    workitem_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    if workitem_id not in store.workitems:
        raise HTTPException(status_code=404, detail="Workitem not found")
    trace_id = store.workitems[workitem_id].trace_id
    return to_dict(store.traces[trace_id])


@app.get("/v1/artifacts/{artifact_id}/content")
def get_artifact_content(
    artifact_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    try:
        artifact = store.find_artifact(artifact_id)
        content = artifact_store.read(artifact_id)
        return {
            "artifact": to_dict(artifact),
            **content,
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Artifact content {artifact_id} not found") from exc


class AcceptancePayload(BaseModel):
    criterion_id: str
    checked: bool
    actor: str | None = None


@app.post("/v1/workitems/{workitem_id}/acceptance")
def update_acceptance(
    workitem_id: str,
    body: AcceptancePayload,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if workitem_id not in store.workitems:
        raise HTTPException(status_code=404, detail="Workitem not found")
    try:
        result = store.append_acceptance_check(
            workitem_id, body.criterion_id, body.checked, body.actor or user.id
        )
        store._persist()  # noqa: SLF001 - acceptance 也属于状态变更
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Criterion {exc} not found") from exc


@app.post("/v1/workflows/{workflow_id}/{trigger}")
def workflow_action(
    workflow_id: str,
    trigger: str,
    body: ActionPayload,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if workflow_id not in store.workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    required = f"workflow.{trigger}"
    allowed = ROLE_PERMISSIONS.get(user.role, set())
    if required not in allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": f"role '{user.role}' lacks permission for {required}",
                "required": required,
            },
        )
    try:
        result = store.apply_workflow_action(workflow_id, trigger, body.actor or user.id, body.payload)
        store._persist()  # noqa: SLF001
        return result
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/audit")
def list_audit(
    limit: int = 50,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return store.list_audit(limit)


# ---------------------------------------------------------------------------
# OBS-01: 可观测性聚合
# ---------------------------------------------------------------------------
@app.get("/v1/metrics")
def get_metrics(user: User = Depends(require_permission("read"))) -> dict[str, Any]:
    return aggregate_metrics(store)


@app.get("/v1/alerts")
def get_alerts(
    limit: int = 50,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return list_alerts(store, limit=limit)


@app.get("/v1/alerts/outbox")
def get_alert_outbox(
    limit: int = 50,
    status: str | None = None,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    items = alert_router.list_outbox(limit)
    if status:
        items = [item for item in items if item.get("status") == status]
    return items


@app.get("/v1/alerts/outbox/clusters")
def get_alert_outbox_clusters(
    limit: int = 50,
    status: str | None = None,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return alert_router.cluster_outbox(limit=limit, status=status)


@app.get("/v1/alerts/outbox/silences")
def get_alert_outbox_silences(
    limit: int = 50,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return alert_router.list_silenced_clusters(limit=limit)


@app.post("/v1/alerts/outbox/{alert_id}/resend")
def resend_alert(
    alert_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    try:
        record = alert_router.resend(alert_id)
        task = _maybe_create_alert_ops_task(
            actor=user.id,
            title=f"[Ops] Resent alert {record.get('event_name', alert_id)}",
            summary=(
                f"Operator resent alert {alert_id}. "
                f"status={record.get('status')} provider={record.get('provider')} "
                f"attempts={record.get('attempts')}"
            ),
            source_kind="alert.resend",
            source_ref=alert_id,
            records=[record],
            metadata={"alert_id": alert_id, "status": record.get("status")},
        )
        if task:
            record["ops_task"] = task
        return record
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found") from exc


@app.post("/v1/alerts/outbox/resend")
def resend_alerts(
    body: AlertBatchResendPayload,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    records = alert_router.resend_many(
        alert_ids=body.alert_ids or None,
        status=body.status,
        cluster_key=body.cluster_key,
    )
    if records:
        task_title = "[Ops] Batch resent alert outbox"
        task_summary = (
            f"Operator batch resent {len(records)} alerts. "
            f"filter_status={body.status or '-'} cluster_key={body.cluster_key or '-'} "
            f"alerts={','.join(record['id'] for record in records[:5])}"
        )
        _maybe_create_alert_ops_task(
            actor=user.id,
            title=task_title,
            summary=task_summary,
            source_kind="alert.resend_many",
            source_ref=body.cluster_key or body.status or ",".join(record["id"] for record in records[:5]),
            records=records,
            metadata={
                "status": body.status,
                "cluster_key": body.cluster_key,
                "alert_ids": [record["id"] for record in records],
            },
        )
    return records


@app.post("/v1/alerts/outbox/silence")
def silence_alert_cluster(
    body: AlertSilencePayload,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    result = alert_router.silence_cluster(body.cluster_key, duration_sec=body.duration_sec)
    records = alert_router.list_cluster_records(body.cluster_key)
    task = _maybe_create_alert_ops_task(
        actor=user.id,
        title=f"[Ops] Silenced alert cluster {records[0].get('event_name', body.cluster_key) if records else body.cluster_key}",
        summary=(
            f"Operator silenced cluster {body.cluster_key} for {int(body.duration_sec or runtime_settings.alert_silence_default_sec)}s. "
            f"until={result.get('silenced_until')}"
        ),
        source_kind="alert.silence",
        source_ref=body.cluster_key,
        records=records,
        metadata={"cluster_key": body.cluster_key, "silenced_until": result.get("silenced_until")},
    )
    if task:
        result["ops_task"] = task
    return result


@app.post("/v1/alerts/outbox/unsilence")
def unsilence_alert_cluster(
    body: AlertSilencePayload,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    records = alert_router.list_cluster_records(body.cluster_key)
    result = alert_router.unsilence_cluster(body.cluster_key)
    task = _maybe_create_alert_ops_task(
        actor=user.id,
        title=f"[Ops] Unsilenced alert cluster {records[0].get('event_name', body.cluster_key) if records else body.cluster_key}",
        summary=f"Operator unsilenced cluster {body.cluster_key}. cleared={result.get('cleared')}",
        source_kind="alert.unsilence",
        source_ref=body.cluster_key,
        records=records,
        metadata={"cluster_key": body.cluster_key, "cleared": result.get("cleared")},
    )
    if task:
        result["ops_task"] = task
    return result


@app.post("/v1/alerts/outbox/unsilence-many")
def unsilence_alert_clusters(
    body: AlertSilenceBatchPayload,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for cluster_key in body.cluster_keys:
        records.extend(alert_router.list_cluster_records(cluster_key))
    result = alert_router.unsilence_many(body.cluster_keys)
    if records:
        _maybe_create_alert_ops_task(
            actor=user.id,
            title="[Ops] Batch unsilenced alert clusters",
            summary=(
                f"Operator unsilenced {len(body.cluster_keys)} clusters. "
                f"clusters={','.join(body.cluster_keys[:5])}"
            ),
            source_kind="alert.unsilence_many",
            source_ref=",".join(body.cluster_keys[:5]),
            records=records,
            metadata={"cluster_keys": body.cluster_keys},
        )
    return result


@app.get("/v1/ops/tasks")
def list_ops_tasks(
    limit: int = 50,
    workitem_id: str | None = None,
    source_kind: str | None = None,
    force_refresh: bool = False,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return store.list_ops_tasks(
        limit=limit,
        workitem_id=workitem_id,
        source_kind=source_kind,
        force_refresh=force_refresh,
    )


@app.get("/v1/workflows/{workflow_id}/overview")
def get_workflow_overview(
    workflow_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    try:
        return workflow_overview(store, workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Workflow {exc} not found") from exc


class AgentRunPayload(BaseModel):
    actor: str | None = None
    payload: dict[str, Any] = {}


@app.post("/v1/workitems/{workitem_id}/run-agent")
def run_agent(
    workitem_id: str,
    body: AgentRunPayload,
    user: User = Depends(require_permission("run_agent")),
) -> dict[str, Any]:
    if workitem_id not in store.workitems:
        raise HTTPException(status_code=404, detail="Workitem not found")
    try:
        result = execution_gateway.run_agent(
            store,
            workitem_id,
            body.actor or user.id,
            body.payload,
        )
        store._persist()  # noqa: SLF001
        return result
    except IdempotencyConflict as exc:
        # 409 + 结构化错误体，前端可静默忽略避免连点报错
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "op": exc.op,
                "workitem_id": exc.workitem_id,
                "message": str(exc),
            },
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/jobs")
def list_jobs(user: User = Depends(require_permission("read"))) -> list[dict[str, Any]]:
    return job_manager.list_jobs()


@app.get("/v1/jobs/dead-letters")
def list_dead_letters(user: User = Depends(require_permission("read"))) -> list[dict[str, Any]]:
    return job_manager.list_dead_letters()


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(require_permission("read"))) -> dict[str, Any]:
    try:
        return job_manager.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc


@app.post("/v1/jobs/{job_id}/retry")
def retry_job(job_id: str, user: User = Depends(require_permission("run_agent"))) -> dict[str, Any]:
    try:
        return job_manager.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/jobs/{job_id}/replay")
def replay_job(
    job_id: str,
    body: JobReplayPayload,
    user: User = Depends(require_permission("run_agent")),
) -> dict[str, Any]:
    try:
        return job_manager.replay_job(job_id, payload_override=body.payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc


@app.post("/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_permission("run_agent"))) -> dict[str, Any]:
    try:
        return job_manager.cancel_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/jobs/{job_id}/human-takeover")
def human_takeover_job(
    job_id: str,
    body: ActionPayload,
    user: User = Depends(require_permission("takeover")),
) -> dict[str, Any]:
    try:
        job = job_manager.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc
    workitem_id = job["workitem_id"]
    payload = dict(body.payload)
    payload.setdefault("new_owner", user.id)
    payload.setdefault("note", f"Human takeover from job {job_id}")
    try:
        return store.apply_action(workitem_id, "takeover", user.id, payload)
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/jobs/{job_id}/escalate-task")
def escalate_job_to_task(
    job_id: str,
    body: OpsTaskCreatePayload,
    user: User = Depends(require_permission("escalate")),
) -> dict[str, Any]:
    try:
        job = job_manager.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc
    workitem = store.workitems.get(job["workitem_id"])
    if workitem is None:
        raise HTTPException(status_code=404, detail=f"Workitem {job['workitem_id']} not found")
    transition_result: dict[str, Any] | None = None
    if workitem.state.value != "escalated":
        try:
            transition_result = store.apply_action(
                workitem.id,
                "escalate",
                user.id,
                {"reason": f"Escalated from dead-letter job {job_id}", "source_job_id": job_id},
            )
        except StateTransitionError:
            transition_result = None
    task = store.create_ops_task(
        workitem_id=workitem.id,
        actor=user.id,
        title=body.title or f"[P0] Investigate dead-letter job {job_id}",
        summary=body.summary or (job.get("error") or f"Job {job_id} requires manual follow-up"),
        severity=body.severity,
        source_job_id=job_id,
    )
    return {
        "task": task,
        "workitem": store.workitem_detail(workitem.id),
        "transition": transition_result,
    }


@app.get("/v1/events")
async def stream_events(
    request: Request,
    replay: int = 0,
    user: User = Depends(require_permission("read")),
) -> StreamingResponse:
    async def event_source():
        async for event in bus.subscribe(replay=replay):
            if await request.is_disconnected():
                break
            yield (
                f"id: {event.seq}\n"
                f"event: {event.name}\n"
                f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/v1/events/history")
def event_history(
    limit: int = 50,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return [event.to_dict() for event in bus.history(limit)]


@app.get("/v1/executors")
def list_executors(user: User = Depends(require_permission("read"))) -> list[dict[str, Any]]:
    return store.recommend_executors()


@app.post("/v1/executors/recommend")
def recommend_executors(
    body: RecommendationPayload,
    user: User = Depends(require_permission("read")),
) -> list[dict[str, Any]]:
    return store.recommend_executors(body.capability)


# ---------------------------------------------------------------------------
# TPL-01: 模板 CRUD + instantiate
# ---------------------------------------------------------------------------
@app.get("/v1/templates")
def list_templates(user: User = Depends(require_permission("read"))) -> list[dict[str, Any]]:
    return store.list_templates()


@app.get("/v1/templates/{template_id}")
def get_template(
    template_id: str,
    user: User = Depends(require_permission("read")),
) -> dict[str, Any]:
    try:
        return store.get_template(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found") from exc


@app.post("/v1/templates")
def create_template(
    body: TemplatePayload,
    user: User = Depends(require_permission("template.create")),
) -> dict[str, Any]:
    try:
        return store.create_template(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/v1/templates/{template_id}")
def update_template(
    template_id: str,
    body: TemplatePatch,
    user: User = Depends(require_permission("template.update")),
) -> dict[str, Any]:
    try:
        return store.update_template(template_id, body.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Template {exc} not found") from exc


@app.delete("/v1/templates/{template_id}")
def delete_template(
    template_id: str,
    user: User = Depends(require_permission("template.delete")),
) -> dict[str, str]:
    try:
        store.delete_template(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Template {exc} not found") from exc
    return {"status": "deleted"}


@app.post("/v1/templates/{template_id}/instantiate")
def instantiate_template(
    template_id: str,
    body: TemplateInstantiatePayload,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return store.instantiate_template(template_id, body.title, owner=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Template {exc} not found") from exc


def _apply(workitem_id: str, trigger: str, body: ActionPayload, user: User) -> dict[str, Any]:
    if workitem_id not in store.workitems:
        raise HTTPException(status_code=404, detail="Workitem not found")
    try:
        result = store.apply_action(workitem_id, trigger, body.actor or user.id, body.payload)
        store._persist()  # noqa: SLF001 - 主动持久化，重启可恢复
        return result
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

"""OBJ-02: 工作项与工作流的状态机。

设计原则：
1. 所有状态变更都必须通过 :func:`transition` / :func:`transition_workflow`，禁止
   直接修改 ``state`` 字段。
2. 每次成功转移都会产出一条 :class:`AuditEvent` 与可选的 :class:`TraceEntry`，供
   审计、Trace 流、SSE 等下游消费。
3. Guard 是“硬约束”，例如 reject 必须填 reason、approve 必须验收 100%、决策门
   必须带 decision，违反 guard 直接抛 :class:`StateTransitionError`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import (
    AuditEvent,
    TERMINAL_STATES,
    TraceEntry,
    WORKFLOW_TERMINAL_STATES,
    Workflow,
    WorkflowState,
    Workitem,
    WorkitemState,
    new_id,
    now_iso,
)


class StateTransitionError(ValueError):
    """非法状态转移或未通过 guard 的统一异常。"""


# ---------------------------------------------------------------------------
# Workitem 状态机
# ---------------------------------------------------------------------------
ALLOWED_TRANSITIONS: dict[str, set[WorkitemState]] = {
    # 调度
    "assign": {WorkitemState.QUEUED, WorkitemState.PAUSED, WorkitemState.REJECTED},
    "start": {WorkitemState.QUEUED, WorkitemState.PAUSED},
    # 暂停 / 恢复
    "pause": {WorkitemState.IN_PROGRESS, WorkitemState.AWAITING_DECISION},
    "resume": {WorkitemState.PAUSED},
    # 接管：可在任何非终态接管
    "takeover": {
        WorkitemState.QUEUED,
        WorkitemState.IN_PROGRESS,
        WorkitemState.PAUSED,
        WorkitemState.AWAITING_DECISION,
        WorkitemState.SUBMITTED,
    },
    # 决策门
    "request_decision": {WorkitemState.IN_PROGRESS},
    "decide": {WorkitemState.AWAITING_DECISION},
    # 验收
    "submit": {WorkitemState.IN_PROGRESS},
    "approve": {WorkitemState.SUBMITTED},
    "reject": {WorkitemState.SUBMITTED},
    # 异常
    "escalate": set(WorkitemState) - TERMINAL_STATES,
    "cancel": set(WorkitemState) - TERMINAL_STATES,
}

TARGET_STATES: dict[str, WorkitemState] = {
    "assign": WorkitemState.IN_PROGRESS,
    "start": WorkitemState.IN_PROGRESS,
    "pause": WorkitemState.PAUSED,
    "resume": WorkitemState.IN_PROGRESS,
    "takeover": WorkitemState.IN_PROGRESS,
    "request_decision": WorkitemState.AWAITING_DECISION,
    "decide": WorkitemState.IN_PROGRESS,
    "submit": WorkitemState.SUBMITTED,
    "approve": WorkitemState.APPROVED,
    "reject": WorkitemState.REJECTED,
    "escalate": WorkitemState.ESCALATED,
    "cancel": WorkitemState.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class TransitionResult:
    from_state: WorkitemState
    to_state: WorkitemState
    side_effects: list[str]
    audit: AuditEvent
    trace_entry: TraceEntry | None = None
    domain_events: list[str] = field(default_factory=list)


def can_transition(workitem: Workitem, trigger: str) -> bool:
    """供 UI 预检按钮可用性。仅检查状态合法性，不跑 guard。"""
    return trigger in ALLOWED_TRANSITIONS and workitem.state in ALLOWED_TRANSITIONS[trigger]


def transition(
    workitem: Workitem,
    trigger: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> TransitionResult:
    payload = payload or {}
    if trigger not in ALLOWED_TRANSITIONS:
        raise StateTransitionError(f"Unsupported trigger: {trigger}")
    if workitem.state not in ALLOWED_TRANSITIONS[trigger]:
        raise StateTransitionError(
            f"Cannot {trigger} workitem from {workitem.state.value}"
        )

    _run_guards(trigger, workitem, payload, actor)

    from_state = workitem.state
    to_state = TARGET_STATES[trigger]

    # 正式落库前执行副作用记录
    side_effects, domain_events = _side_effects(trigger, workitem, payload)
    _apply_post_transition(trigger, workitem, payload)

    workitem.state = to_state
    workitem.updated_at = now_iso()

    audit = AuditEvent(
        id=new_id("evt"),
        workitem_id=workitem.id,
        workflow_id=workitem.parent_workflow_id,
        actor=actor,
        action=f"workitem.{trigger}",
        from_state=from_state.value,
        to_state=to_state.value,
        payload=payload,
    )
    trace_entry = _make_trace_entry(workitem, trigger, actor, from_state, to_state, payload)

    return TransitionResult(
        from_state=from_state,
        to_state=to_state,
        side_effects=side_effects,
        audit=audit,
        trace_entry=trace_entry,
        domain_events=domain_events,
    )


# ---------------------------------------------------------------------------
# Workflow 状态机（轻量级，PRD §4 OBJ-01 要求工作流也需要状态）
# ---------------------------------------------------------------------------
WORKFLOW_TRANSITIONS: dict[str, set[WorkflowState]] = {
    "start": {WorkflowState.DRAFT, WorkflowState.PAUSED},
    "pause": {WorkflowState.RUNNING},
    "resume": {WorkflowState.PAUSED},
    "complete": {WorkflowState.RUNNING},
    "cancel": set(WorkflowState) - WORKFLOW_TERMINAL_STATES,
}

WORKFLOW_TARGETS: dict[str, WorkflowState] = {
    "start": WorkflowState.RUNNING,
    "pause": WorkflowState.PAUSED,
    "resume": WorkflowState.RUNNING,
    "complete": WorkflowState.COMPLETED,
    "cancel": WorkflowState.CANCELLED,
}


def transition_workflow(
    workflow: Workflow,
    trigger: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    payload = payload or {}
    if trigger not in WORKFLOW_TRANSITIONS:
        raise StateTransitionError(f"Unsupported workflow trigger: {trigger}")
    if workflow.state not in WORKFLOW_TRANSITIONS[trigger]:
        raise StateTransitionError(
            f"Cannot {trigger} workflow from {workflow.state.value}"
        )
    from_state = workflow.state
    workflow.state = WORKFLOW_TARGETS[trigger]
    workflow.updated_at = now_iso()
    return AuditEvent(
        id=new_id("evt"),
        workitem_id=None,
        workflow_id=workflow.id,
        actor=actor,
        action=f"workflow.{trigger}",
        from_state=from_state.value,
        to_state=workflow.state.value,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Guards & side-effects
# ---------------------------------------------------------------------------
def _run_guards(trigger: str, workitem: Workitem, payload: dict[str, Any], actor: str) -> None:
    if trigger == "reject" and not payload.get("reason"):
        raise StateTransitionError("Reject requires a reason")
    if trigger == "approve":
        if workitem.acceptance_progress < 1:
            raise StateTransitionError(
                "Approve requires 100% checked acceptance criteria"
            )
        if workitem.owner != actor and not payload.get("force"):
            raise StateTransitionError("Approve must be performed by the owner")
    if trigger == "decide" and workitem.decision_gate and not payload.get("decision"):
        raise StateTransitionError("Decision gate requires a selected decision")
    if trigger == "submit" and workitem.budget.is_exhausted and not payload.get("override_budget"):
        raise StateTransitionError("Cannot submit while budget is exhausted, request override")
    if trigger == "takeover" and not payload.get("new_owner") and not payload.get("note"):
        # 接管必须留迹：要么换 owner，要么留备注
        raise StateTransitionError("Takeover requires new_owner or note for audit trail")


def _side_effects(
    trigger: str, workitem: Workitem, payload: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """返回 ``(human-readable side effects, domain event names)``。"""
    domain = [f"workitem.{trigger}"]
    if trigger == "assign" or trigger == "start":
        return ["trace.start", "event.workitem.state_changed"], domain + ["workitem.started"]
    if trigger == "request_decision":
        return [
            "event.decision.requested",
            f"notify.owner:{workitem.owner}",
        ], domain + ["decision.requested"]
    if trigger == "decide":
        return [
            "event.decision.resolved",
            f"trace.decision:{payload.get('decision')}",
        ], domain + ["decision.resolved"]
    if trigger == "submit":
        return ["event.workitem.submitted"], domain + ["workitem.submitted"]
    if trigger == "approve":
        return ["event.workitem.approved", "artifact.publish"], domain + ["workitem.approved"]
    if trigger == "reject":
        return [
            "trace.reject_reason.append",
            "event.workitem.rejected",
        ], domain + ["workitem.rejected"]
    if trigger == "escalate":
        return ["risk.detected", f"notify.owner:{workitem.owner}"], domain + [
            "workitem.escalated",
        ]
    if trigger == "takeover":
        return [
            f"notify.owner:{payload.get('new_owner') or workitem.owner}",
            "event.workitem.takeover",
        ], domain + ["workitem.takeover"]
    if trigger == "pause":
        return ["event.workitem.paused"], domain + ["workitem.paused"]
    if trigger == "resume":
        return ["event.workitem.resumed"], domain + ["workitem.resumed"]
    if trigger == "cancel":
        return ["event.workitem.cancelled"], domain + ["workitem.cancelled"]
    return ["event.workitem.state_changed"], domain


def _apply_post_transition(trigger: str, workitem: Workitem, payload: dict[str, Any]) -> None:
    """把状态相关的字段写回 Workitem，例如时间戳、拒绝历史、决策选项等。"""
    if trigger in {"assign", "start"} and workitem.started_at is None:
        workitem.started_at = now_iso()
    if trigger == "approve":
        workitem.completed_at = now_iso()
    if trigger in {"reject", "cancel"}:
        workitem.completed_at = now_iso()
    if trigger == "reject":
        workitem.rejection_history.append(
            {
                "reason": payload.get("reason"),
                "actor": payload.get("actor"),
                "timestamp": now_iso(),
            }
        )
    if trigger == "decide" and workitem.decision is not None:
        workitem.decision.selected_option = payload.get("decision")
        workitem.decision.reasoning = payload.get("reasoning")
    if trigger == "takeover" and payload.get("new_owner"):
        workitem.owner = payload["new_owner"]


def _make_trace_entry(
    workitem: Workitem,
    trigger: str,
    actor: str,
    from_state: WorkitemState,
    to_state: WorkitemState,
    payload: dict[str, Any],
) -> TraceEntry:
    return TraceEntry(
        timestamp=now_iso(),
        actor=actor,
        action=f"workitem.{trigger}",
        tool_used=payload.get("tool_used"),
        input_snapshot={"trigger": trigger, "payload": payload},
        output_snapshot={
            "from": from_state.value,
            "to": to_state.value,
        },
        cost=float(payload.get("cost", 0)),
        duration=float(payload.get("duration", 0)),
        status="succeeded",
    )


# ---------------------------------------------------------------------------
# 兼容 API：repository.py 旧版 import 名称
# ---------------------------------------------------------------------------
def make_trace_entry(
    workitem: Workitem,
    trigger: str,
    actor: str,
    result: TransitionResult,
    payload: dict[str, Any] | None = None,
) -> TraceEntry:
    """旧 API 留作兼容：直接复用 transition 已经构造好的 trace_entry。"""
    return result.trace_entry or _make_trace_entry(
        workitem, trigger, actor, result.from_state, result.to_state, payload or {}
    )

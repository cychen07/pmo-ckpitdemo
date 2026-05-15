"""OBS-01 / WF-CTL: 可观测性聚合。

从内存 Store 提取面板所需的聚合指标：
- 工作项状态分布、在跑数、待决策、超时数
- 预算消耗汇总（cost / token / time）
- Agent 维度成本与产物计数
- 近期告警（来自 EventBus 的 budget.* 与 workitem.escalated）
- 工作流总览（total nodes / done / blocked / decision_pending / 进度百分比）
"""
from __future__ import annotations

from typing import Any

from .domain.models import Workitem, WorkitemState
from .events import bus
from .repository import InMemoryStore


# 哪些事件名被识别为告警
ALERT_EVENT_PREFIXES: tuple[str, ...] = (
    "budget.warning",
    "budget.exhausted",
    "workitem.escalated",
    "agent.followup.failed",
    "job.dead_lettered",
)


def _budget_pct(used: float, cap: float) -> int:
    if not cap:
        return 0
    return int(round(used / cap * 100))


def aggregate_metrics(store: InMemoryStore) -> dict[str, Any]:
    workitems = list(store.workitems.values())
    state_counts: dict[str, int] = {s.value: 0 for s in WorkitemState}
    total_tokens_used = 0
    total_tokens_cap = 0
    total_cost_used = 0.0
    total_cost_cap = 0.0
    total_time_used = 0
    total_time_cap = 0
    agent_costs: dict[str, dict[str, Any]] = {}
    artifact_count = 0
    decision_pending = 0
    over_budget = 0

    for wi in workitems:
        state_counts[wi.state.value] = state_counts.get(wi.state.value, 0) + 1
        b = wi.budget
        total_tokens_used += b.tokens_used
        total_tokens_cap += b.token_cap
        total_cost_used += b.cost_used_usd
        total_cost_cap += b.cost_cap_usd
        total_time_used += b.time_used_sec
        total_time_cap += b.time_cap_min * 60
        artifact_count += len(wi.artifacts)
        if wi.state == WorkitemState.AWAITING_DECISION:
            decision_pending += 1
        if b.is_exhausted:
            over_budget += 1
        if wi.assignee.type.value == "agent":
            slot = agent_costs.setdefault(
                wi.assignee.id,
                {
                    "executor_id": wi.assignee.id,
                    "name": wi.assignee.name,
                    "tokens_used": 0,
                    "cost_used_usd": 0.0,
                    "workitems": 0,
                    "artifacts": 0,
                },
            )
            slot["tokens_used"] += b.tokens_used
            slot["cost_used_usd"] = round(slot["cost_used_usd"] + b.cost_used_usd, 4)
            slot["workitems"] += 1
            slot["artifacts"] += len(wi.artifacts)

    workflows = []
    for wf_id in store.workflows:
        workflows.append(workflow_overview(store, wf_id))

    # 近期告警（从最新 history 提取，最多 20 条）
    history = bus.history(limit=200)
    alerts: list[dict[str, Any]] = []
    for ev in reversed(history):
        if any(ev.name.startswith(p) for p in ALERT_EVENT_PREFIXES):
            alerts.append(ev.to_dict())
            if len(alerts) >= 20:
                break

    # 事件总数 / 各事件类型计数（取最近 200 条）
    event_kind_count: dict[str, int] = {}
    for ev in history:
        prefix = ev.name.split(".", 1)[0]
        event_kind_count[prefix] = event_kind_count.get(prefix, 0) + 1

    return {
        "summary": {
            "workitems_total": len(workitems),
            "in_progress": state_counts.get("in_progress", 0),
            "awaiting_decision": decision_pending,
            "approved": state_counts.get("approved", 0),
            "escalated": state_counts.get("escalated", 0),
            "over_budget": over_budget,
            "artifact_count": artifact_count,
            "executor_count": len(store.executors),
            "workflow_count": len(store.workflows),
            "template_count": len(store.templates),
            "event_count": len(history),
            "alert_count": len(alerts),
        },
        "state_distribution": state_counts,
        "budget": {
            "tokens": {
                "used": total_tokens_used,
                "cap": total_tokens_cap,
                "pct": _budget_pct(total_tokens_used, total_tokens_cap),
            },
            "cost_usd": {
                "used": round(total_cost_used, 4),
                "cap": round(total_cost_cap, 4),
                "pct": _budget_pct(total_cost_used, total_cost_cap),
            },
            "time_sec": {
                "used": total_time_used,
                "cap": total_time_cap,
                "pct": _budget_pct(total_time_used, total_time_cap),
            },
        },
        "agent_costs": sorted(
            agent_costs.values(), key=lambda x: x["cost_used_usd"], reverse=True
        ),
        "workflows": workflows,
        "alerts": alerts,
        "event_kind_count": event_kind_count,
    }


def list_alerts(store: InMemoryStore, limit: int = 50) -> list[dict[str, Any]]:
    history = bus.history(limit=300)
    alerts: list[dict[str, Any]] = []
    for ev in reversed(history):
        if any(ev.name.startswith(p) for p in ALERT_EVENT_PREFIXES):
            data = ev.to_dict()
            wi: Workitem | None = (
                store.workitems.get(ev.workitem_id) if ev.workitem_id else None
            )
            if wi is not None:
                data["workitem_title"] = wi.title
                data["workitem_state"] = wi.state.value
            alerts.append(data)
            if len(alerts) >= limit:
                break
    return alerts


def workflow_overview(store: InMemoryStore, workflow_id: str) -> dict[str, Any]:
    if workflow_id not in store.workflows:
        raise KeyError(workflow_id)
    workflow = store.workflows[workflow_id]
    workitems = [store.workitems[i] for i in workflow.nodes if i in store.workitems]
    total = len(workitems)
    state_count: dict[str, int] = {}
    cost_used = 0.0
    cost_cap = 0.0
    time_used = 0
    time_cap = 0
    tokens_used = 0
    tokens_cap = 0
    decision_pending = 0
    blocked = 0
    completed = 0
    for wi in workitems:
        state_count[wi.state.value] = state_count.get(wi.state.value, 0) + 1
        cost_used += wi.budget.cost_used_usd
        cost_cap += wi.budget.cost_cap_usd
        time_used += wi.budget.time_used_sec
        time_cap += wi.budget.time_cap_min * 60
        tokens_used += wi.budget.tokens_used
        tokens_cap += wi.budget.token_cap
        if wi.state == WorkitemState.AWAITING_DECISION:
            decision_pending += 1
        if wi.state in {WorkitemState.PAUSED, WorkitemState.ESCALATED}:
            blocked += 1
        if wi.state in {WorkitemState.APPROVED, WorkitemState.SUBMITTED}:
            completed += 1
    progress = int(round(completed / total * 100)) if total else 0
    return {
        "id": workflow.id,
        "title": workflow.title,
        "state": workflow.state.value,
        "owner": workflow.owner,
        "sla": workflow.sla,
        "rollback_policy": workflow.rollback_policy,
        "total_nodes": total,
        "completed_nodes": completed,
        "blocked_nodes": blocked,
        "decision_pending": decision_pending,
        "progress_pct": progress,
        "state_distribution": state_count,
        "budget": {
            "cost_used_usd": round(cost_used, 4),
            "cost_cap_usd": round(cost_cap, 4),
            "cost_pct": _budget_pct(cost_used, cost_cap),
            "time_used_sec": time_used,
            "time_cap_sec": time_cap,
            "time_pct": _budget_pct(time_used, time_cap),
            "tokens_used": tokens_used,
            "tokens_cap": tokens_cap,
            "tokens_pct": _budget_pct(tokens_used, tokens_cap),
        },
        "allowed_actions": _workflow_allowed_actions(workflow.state.value),
    }


def _workflow_allowed_actions(state: str) -> list[str]:
    if state == "draft":
        return ["start", "cancel"]
    if state == "running":
        return ["pause", "complete", "cancel"]
    if state == "paused":
        return ["resume", "cancel"]
    return []

from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents import make_adapter_trace_entries, run_workitem_plan
from .artifact_store import artifact_store
from .delivery import delivery_service
from .domain.models import (
    AcceptanceCriterion,
    Artifact,
    AuditEvent,
    Budget,
    Capability,
    DecisionGate,
    Executor,
    ExecutorType,
    Priority,
    Trace,
    TraceEntry,
    Workflow,
    WorkflowEdge,
    WorkflowState,
    WorkflowTemplate,
    WorkflowTemplateNode,
    Workitem,
    WorkitemState,
    new_id,
    now_iso,
)
from .domain.state_machine import (
    StateTransitionError,
    can_transition,
    transition,
    transition_workflow,
)
from .events import bus
from .persistence import JsonSnapshotStore, PostgresSnapshotStore
from .tasking import tasking_service


def to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return to_dict(asdict(value))
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return value


def _pct(used: float, cap: float) -> int:
    """整数百分比，cap=0 视作 0 以避免除零。"""
    if not cap:
        return 0
    return int(round((used / cap) * 100))


def _executor_from_dict(raw: dict[str, Any]) -> Executor:
    return Executor(
        id=raw["id"],
        name=raw["name"],
        type=ExecutorType(raw["type"]),
        capabilities=[Capability(**c) for c in raw.get("capabilities", [])],
        current_load=raw.get("current_load", 0),
        unit_cost=raw.get("unit_cost", 0),
        success_rate=raw.get("success_rate", 0),
        rework_rate=raw.get("rework_rate", 0),
        owner_user_id=raw.get("owner_user_id"),
        agent_spec=raw.get("agent_spec"),
    )


def _template_from_dict(raw: dict[str, Any]) -> WorkflowTemplate:
    return WorkflowTemplate(
        id=raw["id"],
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        nodes=[
            WorkflowTemplateNode(
                role=n.get("role", "research"),
                title=n.get("title", ""),
                goal=n.get("goal", ""),
                decision_gate=bool(n.get("decision_gate", False)),
                risk_score=float(n.get("risk_score", 0.0)),
                priority=Priority(n.get("priority", "P1")),
                inputs=list(n.get("inputs", [])),
                expected_outputs=list(n.get("expected_outputs", [])),
                acceptance_criteria=list(n.get("acceptance_criteria", [])),
                tool_whitelist=list(n.get("tool_whitelist", [])),
                budget=dict(n.get("budget", {})),
            )
            for n in raw.get("nodes", [])
        ],
        edges=list(raw.get("edges", [])),
        sla=raw.get("sla", "P3D"),
        rollback_policy=raw.get("rollback_policy", "human_takeover"),
        owner=raw.get("owner", "u_yang"),
        created_at=raw.get("created_at", now_iso()),
        updated_at=raw.get("updated_at", now_iso()),
    )


def _workitem_from_dict(raw: dict[str, Any], executors: dict[str, Executor]) -> Workitem:
    assignee_raw = raw["assignee"]
    assignee_id = assignee_raw["id"]
    assignee = executors.get(assignee_id) or _executor_from_dict(assignee_raw)
    executors[assignee_id] = assignee
    decision_raw = raw.get("decision")
    decision = (
        DecisionGate(
            id=decision_raw["id"],
            title=decision_raw["title"],
            owner=decision_raw["owner"],
            sla_at=decision_raw["sla_at"],
            options=list(decision_raw.get("options", [])),
            selected_option=decision_raw.get("selected_option"),
            reasoning=decision_raw.get("reasoning"),
        )
        if decision_raw
        else None
    )
    return Workitem(
        id=raw["id"],
        title=raw.get("title", ""),
        goal=raw.get("goal", ""),
        inputs=list(raw.get("inputs", [])),
        expected_outputs=list(raw.get("expected_outputs", [])),
        acceptance_criteria=[
            AcceptanceCriterion(
                id=c["id"],
                label=c.get("label", ""),
                checked=bool(c.get("checked", False)),
                note=c.get("note"),
            )
            for c in raw.get("acceptance_criteria", [])
        ],
        tool_whitelist=list(raw.get("tool_whitelist", [])),
        budget=Budget(
            token_cap=raw.get("budget", {}).get("token_cap", 0),
            cost_cap_usd=raw.get("budget", {}).get("cost_cap_usd", 0),
            time_cap_min=raw.get("budget", {}).get("time_cap_min", 0),
            tokens_used=raw.get("budget", {}).get("tokens_used", 0),
            cost_used_usd=raw.get("budget", {}).get("cost_used_usd", 0.0),
            time_used_sec=raw.get("budget", {}).get("time_used_sec", 0),
        ),
        assignee=assignee,
        owner=raw.get("owner", "u_yang"),
        state=WorkitemState(raw.get("state", "queued")),
        priority=Priority(raw.get("priority", "P1")),
        trace_id=raw.get("trace_id", new_id("trace")),
        parent_workflow_id=raw.get("parent_workflow_id"),
        decision_gate=bool(raw.get("decision_gate", False)),
        decision=decision,
        artifacts=[
            Artifact(
                id=a["id"],
                workitem_id=a["workitem_id"],
                type=a.get("type", ""),
                title=a.get("title", ""),
                uri=a.get("uri", ""),
                confidence=a.get("confidence", 1.0),
                version=a.get("version", 1),
                external_refs=list(a.get("external_refs", [])),
                created_at=a.get("created_at", now_iso()),
            )
            for a in raw.get("artifacts", [])
        ],
        rejection_history=list(raw.get("rejection_history", [])),
        risk_score=float(raw.get("risk_score", 0.0)),
        sla_at=raw.get("sla_at"),
        started_at=raw.get("started_at"),
        completed_at=raw.get("completed_at"),
        updated_at=raw.get("updated_at", now_iso()),
    )


def _trace_from_dict(raw: dict[str, Any]) -> Trace:
    return Trace(
        id=raw["id"],
        workitem_id=raw["workitem_id"],
        entries=[
            TraceEntry(
                timestamp=e["timestamp"],
                actor=e["actor"],
                action=e["action"],
                tool_used=e.get("tool_used"),
                input_snapshot=dict(e.get("input_snapshot", {})),
                output_snapshot=dict(e.get("output_snapshot", {})),
                cost=float(e.get("cost", 0)),
                duration=float(e.get("duration", 0)),
                status=e.get("status", "succeeded"),
            )
            for e in raw.get("entries", [])
        ],
    )


# BUD-01 预算护栏阈值
BUDGET_WARN_THRESHOLD = 0.8


class IdempotencyConflict(Exception):
    """该 workitem 已有同名操作在执行中，拒绝重复触发。"""

    def __init__(self, workitem_id: str, op: str) -> None:
        super().__init__(f"{op} on {workitem_id} is already in flight")
        self.workitem_id = workitem_id
        self.op = op


class InMemoryStore:
    def __init__(self, snapshot_path: str | Path | None = None) -> None:
        self.executors: dict[str, Executor] = {}
        self.workitems: dict[str, Workitem] = {}
        self.workflows: dict[str, Workflow] = {}
        self.templates: dict[str, WorkflowTemplate] = {}
        self.traces: dict[str, Trace] = {}
        self.audit_log: list[AuditEvent] = []
        self.ops_tasks: list[dict[str, Any]] = []
        # 幂等：同一个 workitem 同一个 op 同时只允许一份在跑
        self._inflight_lock = threading.Lock()
        self._inflight: set[tuple[str, str]] = set()
        # PER-01：JSON 快照路径，None 表示禁用持久化
        self._snapshot_path: Path | None = Path(snapshot_path) if snapshot_path else None
        self._snapshot_key: str = "default"
        self._persistence_mode: str = "json_snapshot"
        self._postgres_dsn: str | None = None
        self._persist_lock = threading.Lock()
        self.seed_competitive_analysis()
        self.seed_templates()

    # ------------------------------------------------------------------
    # Seed
    # ------------------------------------------------------------------
    def seed_competitive_analysis(self) -> None:
        research_agent = Executor(
            id="agent_research_01",
            name="Research Scout",
            type=ExecutorType.AGENT,
            capabilities=[Capability("竞品调研", 0.92), Capability("信息检索", 0.95)],
            current_load=35,
            unit_cost=0.08,
            success_rate=0.87,
            rework_rate=0.11,
            owner_user_id="u_yang",
            agent_spec={"kind": "research", "adapter": "openai", "model": "gpt-4o-mini"},
        )
        drafting_agent = Executor(
            id="agent_draft_01",
            name="Draft Pilot",
            type=ExecutorType.AGENT,
            capabilities=[Capability("报告起草", 0.9), Capability("结构化写作", 0.88)],
            current_load=42,
            unit_cost=0.1,
            success_rate=0.84,
            rework_rate=0.14,
            owner_user_id="u_yang",
            agent_spec={"kind": "drafting", "adapter": "openai", "model": "gpt-4o"},
        )
        analysis_agent = Executor(
            id="agent_analysis_01",
            name="Insight Analyst",
            type=ExecutorType.AGENT,
            capabilities=[Capability("差距分析", 0.89), Capability("风险识别", 0.86)],
            current_load=25,
            unit_cost=0.12,
            success_rate=0.9,
            rework_rate=0.09,
            owner_user_id="u_yang",
            agent_spec={"kind": "analysis", "adapter": "anthropic", "model": "claude-3.5"},
        )
        human_owner = Executor(
            id="human_pm_01",
            name="阳哥",
            type=ExecutorType.HUMAN,
            capabilities=[Capability("业务验收", 0.98), Capability("产品判断", 0.95)],
            current_load=70,
            unit_cost=180,
            success_rate=0.96,
            rework_rate=0.05,
        )
        for executor in [research_agent, drafting_agent, analysis_agent, human_owner]:
            self.executors[executor.id] = executor

        workflow_id = "wf_competitive_analysis"
        workitems = [
            Workitem(
                id="wi_collect_competitors",
                title="竞品信息收集",
                goal="收集 3 个核心竞品的定位、功能、价格、目标客户和近期发布动态。",
                inputs=[{"type": "template", "uri": "competitive-analysis-v0"}],
                expected_outputs=[
                    {"type": "table", "format": "markdown", "template": "competitor_matrix"}
                ],
                acceptance_criteria=[
                    AcceptanceCriterion("ac_1", "至少覆盖 3 个竞品", True),
                    AcceptanceCriterion("ac_2", "每个竞品包含功能/价格/客户维度", False),
                ],
                tool_whitelist=["web_search", "browser_read", "lark_doc"],
                budget=Budget(token_cap=30000, cost_cap_usd=12, time_cap_min=90),
                assignee=research_agent,
                owner="u_yang",
                state=WorkitemState.IN_PROGRESS,
                priority=Priority.P0,
                trace_id="trace_collect_competitors",
                parent_workflow_id=workflow_id,
                risk_score=0.2,
            ),
            Workitem(
                id="wi_draft_report",
                title="竞品分析报告起草",
                goal="基于调研材料生成一版可评审的竞品分析报告。",
                inputs=[{"type": "workitem", "id": "wi_collect_competitors"}],
                expected_outputs=[
                    {"type": "doc", "format": "markdown", "template": "analysis_report"}
                ],
                acceptance_criteria=[
                    AcceptanceCriterion("ac_3", "包含摘要、对比矩阵、机会点", False),
                    AcceptanceCriterion("ac_4", "结论可被产品例会直接讨论", False),
                ],
                tool_whitelist=["lark_doc", "template_engine"],
                budget=Budget(token_cap=24000, cost_cap_usd=10, time_cap_min=60),
                assignee=drafting_agent,
                owner="u_yang",
                state=WorkitemState.QUEUED,
                priority=Priority.P1,
                trace_id="trace_draft_report",
                parent_workflow_id=workflow_id,
                risk_score=0.1,
            ),
            Workitem(
                id="wi_gap_analysis",
                title="机会点与风险分析",
                goal="提炼产品差异化机会、上线风险与建议决策。",
                inputs=[{"type": "workitem", "id": "wi_draft_report"}],
                expected_outputs=[{"type": "decision_brief", "format": "json"}],
                acceptance_criteria=[
                    AcceptanceCriterion("ac_5", "输出 Top 5 机会点", False),
                    AcceptanceCriterion("ac_6", "标注置信度和证据引用", False),
                ],
                tool_whitelist=["analysis_engine", "lark_doc"],
                budget=Budget(token_cap=20000, cost_cap_usd=8, time_cap_min=45),
                assignee=analysis_agent,
                owner="u_yang",
                state=WorkitemState.AWAITING_DECISION,
                priority=Priority.P1,
                trace_id="trace_gap_analysis",
                parent_workflow_id=workflow_id,
                decision_gate=True,
                risk_score=0.4,
            ),
        ]
        for item in workitems:
            self.workitems[item.id] = item
            self.traces[item.trace_id] = Trace(id=item.trace_id, workitem_id=item.id)

        self.workflows[workflow_id] = Workflow(
            id=workflow_id,
            title="竞品分析模板 MVP",
            nodes=[item.id for item in workitems],
            edges=[
                WorkflowEdge("wi_collect_competitors", "wi_draft_report"),
                WorkflowEdge("wi_draft_report", "wi_gap_analysis"),
            ],
            template_id="tpl_competitive_analysis",
            sla="P3D",
            rollback_policy="human_takeover",
        )

    # ------------------------------------------------------------------
    # Read APIs
    # ------------------------------------------------------------------
    def list_workflows(self) -> list[dict[str, Any]]:
        return [self.workflow_detail(workflow_id) for workflow_id in self.workflows]

    def workflow_detail(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.workflows[workflow_id]
        data = to_dict(workflow)
        data["nodes"] = [self.workitem_detail(item_id) for item_id in workflow.nodes]
        data["executors"] = [to_dict(executor) for executor in self.executors.values()]
        return data

    def workitem_detail(self, workitem_id: str) -> dict[str, Any]:
        workitem = self.workitems[workitem_id]
        data = to_dict(workitem)
        data["allowed_actions"] = [
            trigger
            for trigger in (
                "assign",
                "start",
                "pause",
                "resume",
                "takeover",
                "request_decision",
                "decide",
                "submit",
                "approve",
                "reject",
                "escalate",
                "cancel",
            )
            if can_transition(workitem, trigger)
        ]
        return data

    def find_artifact(self, artifact_id: str) -> Artifact:
        for workitem in self.workitems.values():
            for artifact in workitem.artifacts:
                if artifact.id == artifact_id:
                    return artifact
        raise KeyError(artifact_id)

    def create_workflow(
        self,
        *,
        title: str,
        owner: str = "u_yang",
        template_id: str | None = None,
        sla: str = "P3D",
        rollback_policy: str = "human_takeover",
    ) -> dict[str, Any]:
        if template_id:
            return self.instantiate_template(template_id, title=title, owner=owner)
        workflow = Workflow(
            id=new_id("wf"),
            title=title,
            nodes=[],
            edges=[],
            template_id=None,
            sla=sla,
            rollback_policy=rollback_policy,
            owner=owner,
            state=WorkflowState.DRAFT,
        )
        self.workflows[workflow.id] = workflow
        self._persist()
        return self.workflow_detail(workflow.id)

    # ------------------------------------------------------------------
    # Write APIs
    # ------------------------------------------------------------------
    def apply_action(
        self,
        workitem_id: str,
        trigger: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workitem = self.workitems[workitem_id]
        result = transition(workitem, trigger, actor, payload)
        if result.trace_entry is not None:
            self.traces[workitem.trace_id].append(result.trace_entry)
        self.audit_log.append(result.audit)
        for event_name in result.domain_events:
            evt_payload: dict[str, Any] = {
                "from": result.from_state.value,
                "to": result.to_state.value,
                "actor": actor,
            }
            # decision.* 事件携带选项、原因，前端 toast/drawer 用得上
            if event_name in {"decision.requested", "decision.resolved"} and workitem.decision:
                evt_payload["decision"] = {
                    "id": workitem.decision.id,
                    "title": workitem.decision.title,
                    "options": list(workitem.decision.options),
                    "selected_option": workitem.decision.selected_option,
                    "reasoning": workitem.decision.reasoning,
                }
            bus.publish(
                event_name,
                workitem_id=workitem.id,
                workflow_id=workitem.parent_workflow_id,
                payload=evt_payload,
            )

        followup: dict[str, Any] | None = None
        # decide 完成后自动续跑 Agent，让 analysis 走 submit 路径，形成完整闭环
        if trigger == "decide" and not workitem.is_terminal:
            try:
                followup = self.run_agent(workitem.id, actor, payload)
            except Exception as exc:  # noqa: BLE001 - demo 阶段记录即可
                bus.publish(
                    "agent.followup.failed",
                    workitem_id=workitem.id,
                    workflow_id=workitem.parent_workflow_id,
                    payload={"error": str(exc), "trigger": trigger},
                )

        response: dict[str, Any] = {
            "workitem": self.workitem_detail(workitem.id),
            "transition": {
                "from": result.from_state.value,
                "to": result.to_state.value,
                "side_effects": result.side_effects,
                "domain_events": result.domain_events,
            },
            "trace_entry": to_dict(result.trace_entry),
            "audit": to_dict(result.audit),
        }
        if followup is not None:
            response["followup"] = followup
        return response

    def apply_workflow_action(
        self,
        workflow_id: str,
        trigger: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow = self.workflows[workflow_id]
        event = transition_workflow(workflow, trigger, actor, payload)
        self.audit_log.append(event)
        bus.publish(
            event.action,
            workflow_id=workflow.id,
            payload={
                "from": event.from_state,
                "to": event.to_state,
                "actor": actor,
            },
        )
        return {
            "workflow": self.workflow_detail(workflow.id),
            "audit": to_dict(event),
        }

    def _check_budget(self, workitem: Workitem) -> str | None:
        """返回 None / 'warning' / 'exhausted'。"""
        budget = workitem.budget
        if budget.is_exhausted:
            return "exhausted"
        ratios: list[float] = []
        if budget.token_cap:
            ratios.append(budget.tokens_used / budget.token_cap)
        if budget.cost_cap_usd:
            ratios.append(budget.cost_used_usd / budget.cost_cap_usd)
        if budget.time_cap_min:
            ratios.append(budget.time_used_sec / (budget.time_cap_min * 60))
        if ratios and max(ratios) >= BUDGET_WARN_THRESHOLD:
            return "warning"
        return None

    def _acquire_inflight(self, workitem_id: str, op: str) -> None:
        with self._inflight_lock:
            key = (workitem_id, op)
            if key in self._inflight:
                raise IdempotencyConflict(workitem_id, op)
            self._inflight.add(key)

    def _release_inflight(self, workitem_id: str, op: str) -> None:
        with self._inflight_lock:
            self._inflight.discard((workitem_id, op))

    def _store_artifact_content(
        self,
        artifact: Artifact,
        workitem: Workitem,
        adapter_name: str,
        result_payload: dict[str, Any],
    ) -> None:
        content, content_type = self._render_artifact_content(
            artifact, workitem, adapter_name, result_payload
        )
        artifact_store.save(
            artifact.id,
            content=content,
            content_type=content_type,
        )
        artifact.uri = f"/v1/artifacts/{artifact.id}/content"

    def _render_artifact_content(
        self,
        artifact: Artifact,
        workitem: Workitem,
        adapter_name: str,
        result_payload: dict[str, Any],
    ) -> tuple[str, str]:
        if artifact.type == "table":
            return (
                "\n".join(
                    [
                        f"# {artifact.title}",
                        "",
                        "| 竞品 | 定位 | 价格 | 客户 | 核心特征 |",
                        "|---|---|---|---|---|",
                        "| A | 企业级 | $$$ | 大中型企业 | 全栈方案 |",
                        "| B | 成长期 | $$ | 中小团队 | 快速部署 |",
                        "| C | 开发者向 | $ | 技术团队 | 可扩展 API |",
                        "",
                        f"summary: {result_payload.get('summary', '')}",
                    ]
                ),
                "text/markdown; charset=utf-8",
            )
        if artifact.type in {"doc", "final_report"}:
            return (
                "\n".join(
                    [
                        f"# {artifact.title}",
                        "",
                        f"- workitem: {workitem.title}",
                        f"- agent: {adapter_name}",
                        f"- summary: {result_payload.get('summary', '')}",
                        "",
                        "## 内容",
                        "本文件由 NewEra 后端以结构化文本真实落盘，供前端预览与下载。",
                    ]
                ),
                "text/markdown; charset=utf-8",
            )
        if artifact.type == "decision_brief":
            return (
                json.dumps(
                    {
                        "title": artifact.title,
                        "workitem_id": workitem.id,
                        "summary": result_payload.get("summary", ""),
                        "selected_option": result_payload.get("selected_option"),
                        "top_opportunities": [
                            {"name": "企业中台", "confidence": 0.82},
                            {"name": "开发者生态", "confidence": 0.71},
                            {"name": "行业模板", "confidence": 0.67},
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "application/json",
            )
        return (
            json.dumps(
                {
                    "artifact_id": artifact.id,
                    "title": artifact.title,
                    "workitem_id": workitem.id,
                    "summary": result_payload.get("summary", ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "application/json",
        )

    def run_agent(
        self,
        workitem_id: str,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """同步执行工作项的 Agent。

        步骤：
        1. 通过 run_workitem_plan 解析 Adapter 与是否需要先 start。
        2. 若处于 QUEUED/PAUSED，先做一次 ``start`` 状态转移。
        3. 调用 Adapter，落地 artifacts、trace、预算消耗。
        4. 根据 ``next_trigger`` 再做一次状态转移（submit / request_decision / escalate）。

        幂等保护：同一 workitem 不允许两份 run_agent 并发；终态 / awaiting_decision
        直接拒绝重复触发。
        """
        workitem = self.workitems[workitem_id]

        # 状态前置检查：终态、提交后、等待决策中均拒绝重复跳
        if workitem.is_terminal:
            raise IdempotencyConflict(workitem_id, "run_agent")
        if workitem.state in {WorkitemState.SUBMITTED, WorkitemState.AWAITING_DECISION}:
            raise IdempotencyConflict(workitem_id, "run_agent")

        self._acquire_inflight(workitem_id, "run_agent")
        try:
            plan = run_workitem_plan(workitem)
            adapter = plan["adapter"]

            events: list[dict[str, Any]] = []
            if plan["needs_start"]:
                events.append(self.apply_action(workitem.id, "start", actor, payload or {}))

            result = adapter.run(workitem, payload)
            # 写回预算消耗
            workitem.budget.add_usage(
                tokens=result.tokens_used,
                cost=result.cost_used_usd,
                seconds=result.duration_sec,
            )
            # BUD-01 预算护栏：评估超额情况，按等级发事件 / 改写 next_trigger
            budget_breach = self._check_budget(workitem)
            if budget_breach == "exhausted":
                # 100%+ 超预算：直接 escalate，覆盖 adapter 给的 next_trigger
                bus.publish(
                    "budget.exhausted",
                    workitem_id=workitem.id,
                    workflow_id=workitem.parent_workflow_id,
                    payload={
                        "tokens": [workitem.budget.tokens_used, workitem.budget.token_cap],
                        "cost_usd": [
                            round(workitem.budget.cost_used_usd, 4),
                            workitem.budget.cost_cap_usd,
                        ],
                        "time_sec": [
                            workitem.budget.time_used_sec,
                            workitem.budget.time_cap_min * 60,
                        ],
                    },
                )
                if not workitem.is_terminal and workitem.state != WorkitemState.ESCALATED:
                    result.next_trigger = "escalate"
                    result.payload = {
                        **result.payload,
                        "reason": "budget exhausted, auto-escalated",
                    }
            elif budget_breach == "warning":
                bus.publish(
                    "budget.warning",
                    workitem_id=workitem.id,
                    workflow_id=workitem.parent_workflow_id,
                    payload={
                        "tokens_pct": _pct(
                            workitem.budget.tokens_used, workitem.budget.token_cap
                        ),
                        "cost_pct": _pct(
                            workitem.budget.cost_used_usd, workitem.budget.cost_cap_usd
                        ),
                        "time_pct": _pct(
                            workitem.budget.time_used_sec,
                            workitem.budget.time_cap_min * 60,
                        ),
                    },
                )
            # 落地 artifacts，并把内容真实存入后端存储
            for artifact in result.artifacts:
                self._store_artifact_content(
                    artifact,
                    workitem=workitem,
                    adapter_name=adapter.name,
                    result_payload=result.payload,
                )
                delivery_service.publish_artifact(artifact, workitem)
                delivery_service.notify(
                    workitem,
                    artifact,
                    str(result.payload.get("summary", "")),
                )
            workitem.artifacts.extend(result.artifacts)
            # 写 trace
            trace = self.traces[workitem.trace_id]
            for entry in make_adapter_trace_entries(
                adapter.name,
                result.trace_notes,
                result.cost_used_usd,
                float(result.duration_sec),
            ):
                trace.append(entry)
            # 发出 agent 完成事件
            bus.publish(
                f"agent.{adapter.name}.completed",
                workitem_id=workitem.id,
                workflow_id=workitem.parent_workflow_id,
                payload={
                    "tokens_used": result.tokens_used,
                    "cost_used_usd": result.cost_used_usd,
                    "duration_sec": result.duration_sec,
                    "artifact_ids": [a.id for a in result.artifacts],
                    "summary": result.payload.get("summary"),
                },
            )
            # 让状态机推进到下一档
            next_payload = dict(result.payload or {})
            if result.next_trigger == "request_decision":
                next_payload.setdefault("trigger_source", "agent")
            events.append(
                self.apply_action(workitem.id, result.next_trigger, actor, next_payload)
            )

            return {
                "agent": adapter.name,
                "workitem": self.workitem_detail(workitem.id),
                "events": events,
                "agent_result": {
                    "next_trigger": result.next_trigger,
                    "tokens_used": result.tokens_used,
                    "cost_used_usd": result.cost_used_usd,
                    "duration_sec": result.duration_sec,
                    "artifact_ids": [a.id for a in result.artifacts],
                    "trace_notes": result.trace_notes,
                    "summary": result.payload.get("summary"),
                },
            }
        finally:
            self._release_inflight(workitem_id, "run_agent")

    def append_acceptance_check(
        self, workitem_id: str, criterion_id: str, checked: bool, actor: str
    ) -> dict[str, Any]:
        workitem = self.workitems[workitem_id]
        for criterion in workitem.acceptance_criteria:
            if criterion.id == criterion_id:
                criterion.checked = checked
                break
        else:
            raise KeyError(criterion_id)
        audit = AuditEvent(
            id=new_id("evt"),
            workitem_id=workitem.id,
            workflow_id=workitem.parent_workflow_id,
            actor=actor,
            action="workitem.acceptance.update",
            from_state=workitem.state.value,
            to_state=workitem.state.value,
            payload={"criterion_id": criterion_id, "checked": checked},
        )
        self.audit_log.append(audit)
        return {"workitem": self.workitem_detail(workitem.id), "audit": to_dict(audit)}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def recommend_executors(self, capability: str | None = None) -> list[dict[str, Any]]:
        executors = list(self.executors.values())
        lowered = capability.lower() if capability else None

        def _match_score(e: Executor) -> float:
            if not lowered:
                return 0.0
            return max(
                (cap.confidence for cap in e.capabilities if lowered in cap.tag.lower()),
                default=0.0,
            )

        # RES-01：综合评分 = 能力匹配 0.5 + 通过率 0.3 - 负载/100 * 0.15 - 返工率 0.2 - 单位成本归一 0.05
        max_cost = max((e.unit_cost for e in executors), default=1) or 1

        def _composite(e: Executor) -> float:
            match = _match_score(e)
            return (
                match * 0.5
                + e.success_rate * 0.3
                - (e.current_load / 100) * 0.15
                - e.rework_rate * 0.2
                - (e.unit_cost / max_cost) * 0.05
            )

        executors.sort(key=_composite, reverse=True)

        result: list[dict[str, Any]] = []
        for executor in executors:
            payload = to_dict(executor)
            match = _match_score(executor)
            reasons: list[str] = []
            if lowered and match > 0:
                reasons.append(f"能力匹配 {capability} (置信度 {match:.2f})")
            if executor.success_rate >= 0.9:
                reasons.append(f"高通过率 {executor.success_rate * 100:.0f}%")
            if executor.current_load <= 50:
                reasons.append(f"负载较低 {executor.current_load}%")
            elif executor.current_load >= 80:
                reasons.append(f"负载偏高 {executor.current_load}%")
            if executor.rework_rate <= 0.1:
                reasons.append(f"返工率低 {executor.rework_rate * 100:.0f}%")
            payload["match_score"] = round(match, 3)
            payload["composite_score"] = round(_composite(executor), 3)
            payload["recommend_reasons"] = reasons or ["综合评估候选"]
            result.append(payload)
        return result

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        return [to_dict(event) for event in self.audit_log[-limit:]]

    def create_ops_task(
        self,
        *,
        workitem_id: str,
        actor: str,
        title: str,
        summary: str,
        severity: str = "warning",
        provider: str = "local",
        source_job_id: str | None = None,
        source_kind: str | None = None,
        source_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workitem = self.workitems[workitem_id]
        task = {
            "id": new_id("task"),
            "workitem_id": workitem_id,
            "workflow_id": workitem.parent_workflow_id,
            "title": title,
            "summary": summary,
            "severity": severity,
            "provider": provider,
            "status": "open",
            "owner": workitem.owner,
            "created_by": actor,
            "source_job_id": source_job_id,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "metadata": metadata or {},
            "created_at": now_iso(),
            "delivery_status": "buffered",
            "external_ref": None,
        }
        delivery = tasking_service.create_task(task)
        task["provider"] = delivery.get("provider", provider)
        task["delivery_status"] = delivery.get("delivery_status", "buffered")
        task["external_ref"] = delivery.get("external_ref")
        task["external_task_guid"] = delivery.get("guid")
        task["external_status"] = "delivered" if task["delivery_status"] == "delivered" else "buffered"
        task["last_synced_at"] = now_iso()
        if delivery.get("error"):
            task["delivery_error"] = delivery["error"]
        self.ops_tasks.append(task)
        audit = AuditEvent(
            id=new_id("evt"),
            workitem_id=workitem.id,
            workflow_id=workitem.parent_workflow_id,
            actor=actor,
            action="ops.task.created",
            from_state=workitem.state.value,
            to_state=workitem.state.value,
            payload={
                "task_id": task["id"],
                "severity": severity,
                "source_job_id": source_job_id,
                "source_kind": source_kind,
                "source_ref": source_ref,
            },
        )
        self.audit_log.append(audit)
        bus.publish(
            "ops.task.created",
            workitem_id=workitem.id,
            workflow_id=workitem.parent_workflow_id,
            payload={
                "task_id": task["id"],
                "severity": severity,
                "provider": provider,
                "source_kind": source_kind,
                "source_ref": source_ref,
            },
        )
        self._persist()
        return task

    def list_ops_tasks(
        self,
        limit: int = 50,
        *,
        workitem_id: str | None = None,
        source_kind: str | None = None,
        refresh_status: bool = True,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        items = self.ops_tasks
        if workitem_id:
            items = [item for item in items if item.get("workitem_id") == workitem_id]
        if source_kind:
            items = [item for item in items if item.get("source_kind") == source_kind]
        if refresh_status:
            for item in items:
                self._sync_ops_task(item, stale_after_sec=0.0 if force_refresh else 30.0)
        return items[-limit:][::-1]

    def _sync_ops_task(self, task: dict[str, Any], stale_after_sec: float = 30.0) -> None:
        synced_at_raw = task.get("last_synced_at")
        if isinstance(synced_at_raw, str):
            try:
                synced_at = datetime.fromisoformat(synced_at_raw.replace("Z", "+00:00"))
                if datetime.now(timezone.utc).timestamp() - synced_at.timestamp() < stale_after_sec:
                    return
            except ValueError:
                pass
        update = tasking_service.sync_task(task)
        task["status"] = update.get("status", task.get("status", "open"))
        task["external_status"] = update.get("external_status", task.get("external_status"))
        task["last_synced_at"] = now_iso()
        if update.get("completed_at") is not None:
            task["completed_at"] = update.get("completed_at")
        if update.get("external_ref") is not None:
            task["external_ref"] = update.get("external_ref")
        if update.get("external_task_guid") is not None:
            task["external_task_guid"] = update.get("external_task_guid")
        if update.get("sync_error"):
            task["sync_error"] = update["sync_error"]
        elif "sync_error" in task:
            del task["sync_error"]

    # ------------------------------------------------------------------
    # TPL-01: Template registry
    # ------------------------------------------------------------------
    def seed_templates(self) -> None:
        if "tpl_competitive_analysis" in self.templates:
            return
        self.templates["tpl_competitive_analysis"] = WorkflowTemplate(
            id="tpl_competitive_analysis",
            title="竞品分析模板",
            description="收集 → 起草 → 差距分析（含决策门）",
            nodes=[
                WorkflowTemplateNode(
                    role="research",
                    title="竞品信息收集",
                    goal="收集 3 个核心竞品的定位、功能、价格、目标客户和近期发布动态。",
                    priority=Priority.P0,
                    inputs=[{"type": "template", "uri": "competitive-analysis-v0"}],
                    expected_outputs=[
                        {"type": "table", "format": "markdown", "template": "competitor_matrix"}
                    ],
                    acceptance_criteria=[
                        {"label": "至少覆盖 3 个竞品", "checked": True},
                        {"label": "每个竞品包含功能/价格/客户维度", "checked": False},
                    ],
                    tool_whitelist=["web_search", "browser_read", "lark_doc"],
                    budget={"token_cap": 30000, "cost_cap_usd": 12, "time_cap_min": 90},
                    risk_score=0.2,
                ),
                WorkflowTemplateNode(
                    role="drafting",
                    title="竞品分析报告起草",
                    goal="基于调研材料生成一版可评审的竞品分析报告。",
                    priority=Priority.P1,
                    expected_outputs=[
                        {"type": "doc", "format": "markdown", "template": "analysis_report"}
                    ],
                    acceptance_criteria=[
                        {"label": "包含摘要、对比矩阵、机会点", "checked": False},
                        {"label": "结论可被产品例会直接讨论", "checked": False},
                    ],
                    tool_whitelist=["lark_doc", "template_engine"],
                    budget={"token_cap": 24000, "cost_cap_usd": 10, "time_cap_min": 60},
                    risk_score=0.1,
                ),
                WorkflowTemplateNode(
                    role="analysis",
                    title="机会点与风险分析",
                    goal="提炼产品差异化机会、上线风险与建议决策。",
                    priority=Priority.P1,
                    decision_gate=True,
                    expected_outputs=[{"type": "decision_brief", "format": "json"}],
                    acceptance_criteria=[
                        {"label": "输出 Top 5 机会点", "checked": False},
                        {"label": "标注置信度和证据引用", "checked": False},
                    ],
                    tool_whitelist=["analysis_engine", "lark_doc"],
                    budget={"token_cap": 20000, "cost_cap_usd": 8, "time_cap_min": 45},
                    risk_score=0.4,
                ),
            ],
            edges=[
                {"from": "node_0", "to": "node_1"},
                {"from": "node_1", "to": "node_2"},
            ],
            sla="P3D",
            rollback_policy="human_takeover",
        )

    def list_templates(self) -> list[dict[str, Any]]:
        return [to_dict(tpl) for tpl in self.templates.values()]

    def get_template(self, template_id: str) -> dict[str, Any]:
        if template_id not in self.templates:
            raise KeyError(template_id)
        return to_dict(self.templates[template_id])

    def create_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        tpl_id = payload.get("id") or new_id("tpl")
        if tpl_id in self.templates:
            raise ValueError(f"template {tpl_id} already exists")
        nodes_data = payload.get("nodes") or []
        nodes = [
            WorkflowTemplateNode(
                role=n.get("role", "research"),
                title=n.get("title", "Untitled"),
                goal=n.get("goal", ""),
                decision_gate=bool(n.get("decision_gate", False)),
                risk_score=float(n.get("risk_score", 0.0)),
                priority=Priority(n.get("priority", "P1")),
                inputs=list(n.get("inputs", [])),
                expected_outputs=list(n.get("expected_outputs", [])),
                acceptance_criteria=list(n.get("acceptance_criteria", [])),
                tool_whitelist=list(n.get("tool_whitelist", [])),
                budget=dict(n.get("budget", {"token_cap": 20000, "cost_cap_usd": 8, "time_cap_min": 45})),
            )
            for n in nodes_data
        ]
        tpl = WorkflowTemplate(
            id=tpl_id,
            title=payload.get("title", "Untitled Template"),
            description=payload.get("description", ""),
            nodes=nodes,
            edges=list(payload.get("edges", [])),
            sla=payload.get("sla", "P3D"),
            rollback_policy=payload.get("rollback_policy", "human_takeover"),
            owner=payload.get("owner", "u_yang"),
        )
        self.templates[tpl_id] = tpl
        self._persist()
        return to_dict(tpl)

    def update_template(self, template_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if template_id not in self.templates:
            raise KeyError(template_id)
        tpl = self.templates[template_id]
        for field_name in ("title", "description", "sla", "rollback_policy", "owner"):
            if field_name in patch:
                setattr(tpl, field_name, patch[field_name])
        if "edges" in patch:
            tpl.edges = list(patch["edges"])
        tpl.updated_at = now_iso()
        self._persist()
        return to_dict(tpl)

    def delete_template(self, template_id: str) -> None:
        if template_id not in self.templates:
            raise KeyError(template_id)
        del self.templates[template_id]
        self._persist()

    def instantiate_template(
        self, template_id: str, title: str | None = None, owner: str = "u_yang"
    ) -> dict[str, Any]:
        if template_id not in self.templates:
            raise KeyError(template_id)
        tpl = self.templates[template_id]

        # 按 role 选用现有 executor（取负载最低的同类）
        def pick_executor(role: str) -> Executor:
            kind = ExecutorType.HUMAN if role == "human" else ExecutorType.AGENT
            candidates = [
                e
                for e in self.executors.values()
                if e.type == kind
                and (
                    role == "human"
                    or (e.agent_spec or {}).get("kind") == role
                )
            ]
            if not candidates and kind == ExecutorType.AGENT:
                candidates = [e for e in self.executors.values() if e.type == ExecutorType.AGENT]
            if not candidates:
                candidates = list(self.executors.values())
            candidates.sort(key=lambda e: e.current_load)
            return candidates[0]

        workflow_id = new_id("wf")
        workitem_ids: list[str] = []
        for idx, node in enumerate(tpl.nodes):
            wi_id = new_id("wi")
            trace_id = new_id("trace")
            executor = pick_executor(node.role)
            criteria = [
                AcceptanceCriterion(
                    id=new_id("ac"),
                    label=c.get("label", ""),
                    checked=bool(c.get("checked", False)),
                )
                for c in node.acceptance_criteria
            ]
            budget = Budget(
                token_cap=int(node.budget.get("token_cap", 20000)),
                cost_cap_usd=float(node.budget.get("cost_cap_usd", 8)),
                time_cap_min=int(node.budget.get("time_cap_min", 45)),
            )
            workitem = Workitem(
                id=wi_id,
                title=node.title,
                goal=node.goal,
                inputs=list(node.inputs) if idx == 0 else [{"type": "workitem", "id": workitem_ids[idx - 1]}],
                expected_outputs=list(node.expected_outputs),
                acceptance_criteria=criteria,
                tool_whitelist=list(node.tool_whitelist),
                budget=budget,
                assignee=executor,
                owner=owner,
                state=WorkitemState.QUEUED if idx > 0 else WorkitemState.IN_PROGRESS,
                priority=node.priority,
                trace_id=trace_id,
                parent_workflow_id=workflow_id,
                decision_gate=node.decision_gate,
                risk_score=node.risk_score,
            )
            self.workitems[wi_id] = workitem
            self.traces[trace_id] = Trace(id=trace_id, workitem_id=wi_id)
            workitem_ids.append(wi_id)

        edges: list[WorkflowEdge] = []
        for i in range(len(workitem_ids) - 1):
            edges.append(WorkflowEdge(workitem_ids[i], workitem_ids[i + 1]))

        workflow = Workflow(
            id=workflow_id,
            title=title or tpl.title,
            nodes=workitem_ids,
            edges=edges,
            template_id=tpl.id,
            sla=tpl.sla,
            rollback_policy=tpl.rollback_policy,
            owner=owner,
        )
        self.workflows[workflow_id] = workflow
        self._persist()
        return self.workflow_detail(workflow_id)

    # ------------------------------------------------------------------
    # PER-01: snapshot persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        """写入快照；失败不影响主流程。"""
        try:
            with self._persist_lock:
                snapshot = self.snapshot()
                if self._persistence_mode == "postgres":
                    if not self._postgres_dsn:
                        return
                    PostgresSnapshotStore(self._postgres_dsn).save(
                        snapshot,
                        snapshot_key=self._snapshot_key,
                    )
                else:
                    if self._snapshot_path is None:
                        return
                    JsonSnapshotStore().save(self._snapshot_path, snapshot)
        except Exception:  # noqa: BLE001 - demo 持久化失败仅静默
            pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "saved_at": now_iso(),
            "executors": {k: to_dict(v) for k, v in self.executors.items()},
            "workitems": {k: to_dict(v) for k, v in self.workitems.items()},
            "workflows": {k: to_dict(v) for k, v in self.workflows.items()},
            "templates": {k: to_dict(v) for k, v in self.templates.items()},
            "traces": {k: to_dict(v) for k, v in self.traces.items()},
            "audit_log": [to_dict(e) for e in self.audit_log],
            "ops_tasks": list(self.ops_tasks),
        }

    def configure_persistence(
        self,
        snapshot_path: str | Path,
        *,
        mode: str = "json_snapshot",
        postgres_dsn: str | None = None,
        snapshot_key: str = "default",
    ) -> None:
        self._snapshot_path = Path(snapshot_path)
        self._persistence_mode = mode
        self._postgres_dsn = postgres_dsn
        self._snapshot_key = snapshot_key

    def save_snapshot(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self._snapshot_path
        with self._persist_lock:
            snapshot = self.snapshot()
            if self._persistence_mode == "postgres":
                if not self._postgres_dsn:
                    raise ValueError("postgres_dsn not configured")
                PostgresSnapshotStore(self._postgres_dsn).save(
                    snapshot,
                    snapshot_key=self._snapshot_key,
                )
                return Path("postgres://snapshot")
            if target is None:
                raise ValueError("snapshot_path not configured")
            JsonSnapshotStore().save(target, snapshot)
            return target

    def load_snapshot(self, path: str | Path | None = None) -> bool:
        """从 JSON 文件恢复内存状态；不存在则返回 False。"""
        try:
            if self._persistence_mode == "postgres":
                if not self._postgres_dsn:
                    return False
                data = PostgresSnapshotStore(self._postgres_dsn).load(
                    snapshot_key=self._snapshot_key
                )
                if data is None:
                    return False
            else:
                target = Path(path) if path else self._snapshot_path
                if target is None:
                    return False
                data = JsonSnapshotStore().load(target)
                if data is None:
                    return False
        except Exception:  # noqa: BLE001
            return False

        # 清空再重建
        self.executors.clear()
        self.workitems.clear()
        self.workflows.clear()
        self.templates.clear()
        self.traces.clear()
        self.audit_log.clear()
        self.ops_tasks.clear()

        for eid, raw in data.get("executors", {}).items():
            self.executors[eid] = _executor_from_dict(raw)
        for tid, raw in data.get("templates", {}).items():
            self.templates[tid] = _template_from_dict(raw)

        for wid, raw in data.get("workitems", {}).items():
            self.workitems[wid] = _workitem_from_dict(raw, self.executors)

        for trace_id, raw in data.get("traces", {}).items():
            self.traces[trace_id] = _trace_from_dict(raw)

        for workflow_id, raw in data.get("workflows", {}).items():
            self.workflows[workflow_id] = Workflow(
                id=raw["id"],
                title=raw.get("title", ""),
                nodes=list(raw.get("nodes", [])),
                edges=[
                    WorkflowEdge(
                        from_id=e["from_id"],
                        to_id=e["to_id"],
                        condition=e.get("condition"),
                    )
                    for e in raw.get("edges", [])
                ],
                template_id=raw.get("template_id"),
                sla=raw.get("sla", "P3D"),
                rollback_policy=raw.get("rollback_policy", "human_takeover"),
                owner=raw.get("owner", "u_yang"),
                state=WorkflowState(raw.get("state", "running")),
                created_at=raw.get("created_at", now_iso()),
                updated_at=raw.get("updated_at", now_iso()),
            )

        self.audit_log.extend(
            AuditEvent(
                id=event["id"],
                workitem_id=event.get("workitem_id"),
                workflow_id=event.get("workflow_id"),
                actor=event.get("actor", "system"),
                action=event.get("action", ""),
                from_state=event.get("from_state"),
                to_state=event.get("to_state"),
                payload=dict(event.get("payload", {})),
                timestamp=event.get("timestamp", now_iso()),
            )
            for event in data.get("audit_log", [])
        )
        self.ops_tasks.extend(list(data.get("ops_tasks", [])))

        if not self.executors or not self.workflows or not self.workitems:
            self.seed_competitive_analysis()
        if not self.templates:
            self.seed_templates()
        return True


store = InMemoryStore()

# 向后兼容：若外部想 import `StateTransitionError`
__all__ = [
    "InMemoryStore",
    "store",
    "to_dict",
    "StateTransitionError",
    "IdempotencyConflict",
]

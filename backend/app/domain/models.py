"""OBJ-01: 领域对象模型。

PRD §4 中规定的核心对象：Workitem / Workflow / Executor / Trace / Artifact /
DecisionGate / AcceptanceCriterion / Budget。本文件以 dataclass 表达，配合
``state_machine.py`` 提供 OBJ-02 状态机。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------
class ExecutorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    HYBRID = "hybrid"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class WorkitemState(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    AWAITING_DECISION = "awaiting_decision"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class WorkflowState(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


TERMINAL_STATES: set[WorkitemState] = {
    WorkitemState.APPROVED,
    WorkitemState.REJECTED,
    WorkitemState.CANCELLED,
}

WORKFLOW_TERMINAL_STATES: set[WorkflowState] = {
    WorkflowState.COMPLETED,
    WorkflowState.CANCELLED,
}


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Budget:
    """单工作项预算 + 实时消耗，对应 UI 中三条预算条。"""

    token_cap: int
    cost_cap_usd: float
    time_cap_min: int
    tokens_used: int = 0
    cost_used_usd: float = 0.0
    time_used_sec: int = 0

    def add_usage(self, *, tokens: int = 0, cost: float = 0.0, seconds: int = 0) -> None:
        self.tokens_used += tokens
        self.cost_used_usd += cost
        self.time_used_sec += seconds

    @property
    def is_exhausted(self) -> bool:
        if self.token_cap and self.tokens_used > self.token_cap:
            return True
        if self.cost_cap_usd and self.cost_used_usd > self.cost_cap_usd:
            return True
        if self.time_cap_min and self.time_used_sec > self.time_cap_min * 60:
            return True
        return False


@dataclass(slots=True)
class AcceptanceCriterion:
    id: str
    label: str
    checked: bool = False
    note: str | None = None


@dataclass(slots=True)
class Capability:
    tag: str
    confidence: float


@dataclass(slots=True)
class Executor:
    id: str
    name: str
    type: ExecutorType
    capabilities: list[Capability]
    current_load: int
    unit_cost: float
    success_rate: float
    rework_rate: float
    owner_user_id: str | None = None
    # Agent 专属：工具白名单 + adapter 元信息（PRD §10 AGT-01）
    agent_spec: dict[str, Any] | None = None


@dataclass(slots=True)
class Artifact:
    id: str
    workitem_id: str
    type: str
    title: str
    uri: str
    confidence: float = 1.0
    version: int = 1
    external_refs: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)


@dataclass(slots=True)
class TraceEntry:
    """Trace 即工作项的逐步执行日志。"""

    timestamp: str
    actor: str
    action: str
    tool_used: str | None
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]
    cost: float
    duration: float
    status: Literal["started", "succeeded", "failed", "blocked"]


@dataclass(slots=True)
class Trace:
    id: str
    workitem_id: str
    entries: list[TraceEntry] = field(default_factory=list)

    def append(self, entry: TraceEntry) -> None:
        self.entries.append(entry)


@dataclass(slots=True)
class DecisionGate:
    """决策门，挂在 Workitem 上，对应 UI 中右侧抽屉。"""

    id: str
    title: str
    owner: str
    sla_at: str
    options: list[str]
    selected_option: str | None = None
    reasoning: str | None = None


@dataclass(slots=True)
class WorkflowEdge:
    from_id: str
    to_id: str
    condition: str | None = None


@dataclass(slots=True)
class AuditEvent:
    """审计事件。状态机每次成功转移都会发出至少一条。"""

    id: str
    workitem_id: str | None
    workflow_id: str | None
    actor: str
    action: str
    from_state: str | None
    to_state: str | None
    payload: dict[str, Any]
    timestamp: str = field(default_factory=now_iso)


# ---------------------------------------------------------------------------
# 主体聚合根
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Workitem:
    id: str
    title: str
    goal: str
    inputs: list[dict[str, Any]]
    expected_outputs: list[dict[str, Any]]
    acceptance_criteria: list[AcceptanceCriterion]
    tool_whitelist: list[str]
    budget: Budget
    assignee: Executor
    owner: str
    state: WorkitemState
    priority: Priority
    trace_id: str
    parent_workflow_id: str | None = None
    decision_gate: bool = False
    decision: DecisionGate | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    rejection_history: list[dict[str, Any]] = field(default_factory=list)
    risk_score: float = 0.0
    sla_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str = field(default_factory=now_iso)

    @property
    def acceptance_progress(self) -> float:
        if not self.acceptance_criteria:
            return 0.0
        checked = sum(1 for c in self.acceptance_criteria if c.checked)
        return checked / len(self.acceptance_criteria)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(slots=True)
class Workflow:
    id: str
    title: str
    nodes: list[str]
    edges: list[WorkflowEdge]
    template_id: str | None
    sla: str
    rollback_policy: Literal["retry", "escalate", "human_takeover"]
    owner: str = "u_yang"
    state: WorkflowState = WorkflowState.RUNNING
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @property
    def is_terminal(self) -> bool:
        return self.state in WORKFLOW_TERMINAL_STATES


@dataclass(slots=True)
class WorkflowTemplateNode:
    """TPL-01 模板节点骨架。"""

    role: str  # 'research' | 'drafting' | 'analysis' | 'human'
    title: str
    goal: str
    decision_gate: bool = False
    risk_score: float = 0.0
    priority: Priority = Priority.P1
    inputs: list[dict[str, Any]] = field(default_factory=list)
    expected_outputs: list[dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    tool_whitelist: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=lambda: {
        "token_cap": 20000, "cost_cap_usd": 8, "time_cap_min": 45,
    })


@dataclass(slots=True)
class WorkflowTemplate:
    """TPL-01 工作流模板：可被 instantiate 出新的 workflow。"""

    id: str
    title: str
    description: str
    nodes: list[WorkflowTemplateNode]
    edges: list[dict[str, str]]  # [{from: 'research', to: 'drafting'}]
    sla: str = "P3D"
    rollback_policy: Literal["retry", "escalate", "human_takeover"] = "human_takeover"
    owner: str = "u_yang"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

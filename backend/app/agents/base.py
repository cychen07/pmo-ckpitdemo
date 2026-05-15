"""Agent Adapter 抽象基类与注册表。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..domain.models import Artifact, Workitem


@dataclass(slots=True)
class AgentResult:
    """Adapter 单次执行的结构化产出。"""

    artifacts: list[Artifact]
    trace_notes: list[str]
    tokens_used: int
    cost_used_usd: float
    duration_sec: int
    next_trigger: str  # "submit" / "request_decision" / "escalate"
    payload: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(ABC):
    """所有 Agent 实现的统一契约。"""

    name: str = ""

    @abstractmethod
    def run(
        self, workitem: Workitem, payload: dict[str, Any] | None = None
    ) -> AgentResult:  # pragma: no cover - abstract
        ...


REGISTRY: dict[str, AgentAdapter] = {}


def register(adapter: AgentAdapter) -> AgentAdapter:
    if not adapter.name:
        raise ValueError("AgentAdapter.name is required")
    REGISTRY[adapter.name] = adapter
    return adapter


def resolve(workitem: Workitem) -> AgentAdapter:
    spec = workitem.assignee.agent_spec or {}
    kind = spec.get("kind")
    if not kind:
        raise KeyError(
            f"Executor {workitem.assignee.id} missing agent_spec.kind"
        )
    if kind not in REGISTRY:
        raise KeyError(f"No agent adapter registered for kind={kind!r}")
    return REGISTRY[kind]

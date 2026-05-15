"""Agent Adapter 框架。

PRD §10 AGT-01 落地：把 Executor.agent_spec 解析成可执行的 Adapter，并提供统一的
Runner 把 Adapter 输出接入状态机。
"""
from .base import AgentAdapter, AgentResult, register, resolve
from .research import ResearchAdapter
from .drafting import DraftingAdapter
from .analysis import AnalysisAdapter
from .runner import make_adapter_trace_entries, run_workitem_plan

__all__ = [
    "AgentAdapter",
    "AgentResult",
    "register",
    "resolve",
    "ResearchAdapter",
    "DraftingAdapter",
    "AnalysisAdapter",
    "run_workitem_plan",
    "make_adapter_trace_entries",
]

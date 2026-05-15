"""把 Agent Adapter 接入状态机：start → run → submit/request_decision。"""
from __future__ import annotations

from typing import Any

from ..domain.models import TraceEntry, Workitem, WorkitemState, now_iso
from ..domain.state_machine import StateTransitionError
from . import base


def run_workitem_plan(workitem: Workitem) -> dict[str, Any]:
    """规划但不执行：返回会调用的 adapter 与是否需要先 start。

    供 repository / 测试预检使用。
    """
    if workitem.state in {
        WorkitemState.APPROVED,
        WorkitemState.REJECTED,
        WorkitemState.CANCELLED,
        WorkitemState.SUBMITTED,
        WorkitemState.AWAITING_DECISION,
    }:
        raise StateTransitionError(
            f"Cannot run workitem in state {workitem.state.value}"
        )
    adapter = base.resolve(workitem)
    needs_start = workitem.state in {WorkitemState.QUEUED, WorkitemState.PAUSED}
    return {"adapter": adapter, "needs_start": needs_start}


def make_adapter_trace_entries(
    adapter_name: str, notes: list[str], cost: float, duration: float
) -> list[TraceEntry]:
    """把 Adapter 产出的 trace_notes 转成 TraceEntry，按比例摊费用与时长。"""
    if not notes:
        return []
    each_cost = cost / len(notes)
    each_duration = duration / len(notes)
    entries: list[TraceEntry] = []
    for note in notes:
        entries.append(
            TraceEntry(
                timestamp=now_iso(),
                actor=f"agent:{adapter_name}",
                action="agent.step",
                tool_used=adapter_name,
                input_snapshot={"note": note},
                output_snapshot={"status": "succeeded"},
                cost=each_cost,
                duration=each_duration,
                status="succeeded",
            )
        )
    return entries

"""起草类 Agent：基于上游材料生成结构化文档。"""
from __future__ import annotations

from typing import Any

from ..domain.models import Artifact, Workitem, new_id
from .base import AgentAdapter, AgentResult, register


class DraftingAdapter(AgentAdapter):
    name = "drafting"

    def run(
        self, workitem: Workitem, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        artifact = Artifact(
            id=new_id("art"),
            workitem_id=workitem.id,
            type="doc",
            title=f"{workitem.title} · 草稿 v1",
            uri=f"mock://artifacts/{workitem.id}/draft.md",
            confidence=0.78,
            version=len(workitem.artifacts) + 1,
        )
        notes = [
            "拉取上游竞品矩阵作为输入",
            "套用 analysis_report 模板",
            "生成摘要 / 对比矩阵 / 机会点 三段",
        ]
        return AgentResult(
            artifacts=[artifact],
            trace_notes=notes,
            tokens_used=5800,
            cost_used_usd=2.4,
            duration_sec=58,
            next_trigger="submit",
            payload={
                "summary": "已产出可评审版报告草稿",
                "tool_used": "template_engine",
            },
        )


register(DraftingAdapter())

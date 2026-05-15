"""调研类 Agent：信息采集 + 结构化矩阵。"""
from __future__ import annotations

from typing import Any

from ..domain.models import Artifact, Workitem, new_id
from .base import AgentAdapter, AgentResult, register


class ResearchAdapter(AgentAdapter):
    name = "research"

    def run(
        self, workitem: Workitem, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        artifact = Artifact(
            id=new_id("art"),
            workitem_id=workitem.id,
            type="table",
            title=f"{workitem.title} · 竞品矩阵 v1",
            uri=f"mock://artifacts/{workitem.id}/competitor_matrix.md",
            confidence=0.86,
            version=len(workitem.artifacts) + 1,
        )
        notes = [
            "调用 web_search 抓取 3 个候选竞品",
            "通过 browser_read 扫描官网/定价/客户案例",
            "聚合为对比矩阵草稿",
        ]
        return AgentResult(
            artifacts=[artifact],
            trace_notes=notes,
            tokens_used=4200,
            cost_used_usd=1.85,
            duration_sec=42,
            next_trigger="submit",
            payload={
                "summary": "覆盖 A/B/C 三个核心竞品，含定位/功能/定价/客户维度",
                "tool_used": "web_search",
            },
        )


register(ResearchAdapter())

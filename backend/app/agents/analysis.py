"""分析类 Agent：从草稿中提炼机会点 + 决策建议。

如果工作项带有 ``decision_gate``：
- 首次运行：构造真实的 options + reasoning，发出 ``request_decision``
- 决策已选（``decision.selected_option`` 已写入）：根据选项决定 ``next_trigger``
  （继续 → submit；终止 → reject；微调 → submit + 增订标注）
"""
from __future__ import annotations

from typing import Any

from ..domain.models import Artifact, DecisionGate, Workitem, new_id, now_iso
from .base import AgentAdapter, AgentResult, register


def _build_decision_gate(workitem: Workitem) -> DecisionGate:
    """基于 workitem 的目标 / 验收标准生成更真实的决策选项。"""
    pending_criteria = [c.label for c in workitem.acceptance_criteria if not c.checked]
    reasoning_lines = [
        f"目标: {workitem.goal}",
        f"风险评分: {workitem.risk_score}",
        f"待完成验收: {len(pending_criteria)} 项",
    ]
    if pending_criteria:
        reasoning_lines.append("未达成: " + "; ".join(pending_criteria[:3]))
    options = [
        "继续推进 · 直接产出报告",
        "微调 · 补充 1 轮证据再产出",
        "终止 · 重做调研阶段",
    ]
    return DecisionGate(
        id=new_id("gate"),
        title=f"{workitem.title} · 决策门",
        owner=workitem.owner,
        sla_at=now_iso(),
        options=options,
        reasoning="\n".join(reasoning_lines),
    )


class AnalysisAdapter(AgentAdapter):
    name = "analysis"

    def run(
        self, workitem: Workitem, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        artifact = Artifact(
            id=new_id("art"),
            workitem_id=workitem.id,
            type="decision_brief",
            title=f"{workitem.title} · 决策简报",
            uri=f"mock://artifacts/{workitem.id}/decision_brief.json",
            confidence=0.82,
            version=len(workitem.artifacts) + 1,
        )
        notes = [
            "扫描草稿，定位机会点 Top 5",
            "对每个机会点估算置信度与证据引用",
            "输出决策建议（推荐 / 备选）",
        ]

        decided_option = (
            workitem.decision.selected_option if workitem.decision else None
        )

        if workitem.decision_gate and not decided_option:
            # 第一次跑：构造决策门，等待人来拍板
            if workitem.decision is None:
                workitem.decision = _build_decision_gate(workitem)
            return AgentResult(
                artifacts=[artifact],
                trace_notes=notes,
                tokens_used=3600,
                cost_used_usd=1.6,
                duration_sec=33,
                next_trigger="request_decision",
                payload={
                    "summary": "Top 5 机会点已生成，待决策",
                    "tool_used": "analysis_engine",
                    "options_count": len(workitem.decision.options),
                },
            )

        # 已经拍板：根据选项决定下一步
        next_trigger = "submit"
        decision_summary = f"按决策《{decided_option}》产出最终报告"
        if decided_option and "终止" in decided_option:
            # cancel 允许从 in_progress 转移；reject 要求 submitted，所以这里用 cancel
            next_trigger = "cancel"
            decision_summary = f"按决策《{decided_option}》终止当前阶段"

        # 已决策时，把所有验收项一键勾上（demo 化简）
        for criterion in workitem.acceptance_criteria:
            criterion.checked = True

        followup = Artifact(
            id=new_id("art"),
            workitem_id=workitem.id,
            type="final_report",
            title=f"{workitem.title} · 最终输出 v{len(workitem.artifacts) + 1}",
            uri=f"mock://artifacts/{workitem.id}/final.md",
            confidence=0.91,
            version=len(workitem.artifacts) + 1,
        )

        result_payload: dict[str, Any] = {
            "summary": decision_summary,
            "tool_used": "analysis_engine",
            "selected_option": decided_option,
        }
        if next_trigger == "cancel":
            result_payload["reason"] = f"用户决策终止: {decided_option}"
            result_payload["note"] = "cancel via decision gate"

        return AgentResult(
            artifacts=[followup],
            trace_notes=notes + [f"应用决策：{decided_option}"],
            tokens_used=2200,
            cost_used_usd=1.1,
            duration_sec=21,
            next_trigger=next_trigger,
            payload=result_payload,
        )


register(AnalysisAdapter())

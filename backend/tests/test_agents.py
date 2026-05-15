"""AGT-01 Agent Adapter 单测：覆盖三类 Adapter 行为与 Runner 串接。"""
from __future__ import annotations

import unittest

from backend.app.agents import (
    AnalysisAdapter,
    DraftingAdapter,
    ResearchAdapter,
    resolve,
    run_workitem_plan,
)
from backend.app.agents.base import REGISTRY
from backend.app.domain.models import (
    AcceptanceCriterion,
    Budget,
    Capability,
    Executor,
    ExecutorType,
    Priority,
    Workitem,
    WorkitemState,
)
from backend.app.domain.state_machine import StateTransitionError


def _make_workitem(
    *,
    state: WorkitemState = WorkitemState.QUEUED,
    kind: str | None = "research",
    decision_gate: bool = False,
) -> Workitem:
    spec = {"kind": kind} if kind else None
    executor = Executor(
        id="agent_x",
        name="Agent X",
        type=ExecutorType.AGENT,
        capabilities=[Capability("test", 0.9)],
        current_load=10,
        unit_cost=0.05,
        success_rate=0.9,
        rework_rate=0.1,
        owner_user_id="u_yang",
        agent_spec=spec,
    )
    return Workitem(
        id="wi_test",
        title="Test workitem",
        goal="goal",
        inputs=[],
        expected_outputs=[],
        acceptance_criteria=[AcceptanceCriterion("ac_1", "ok")],
        tool_whitelist=[],
        budget=Budget(token_cap=10000, cost_cap_usd=10, time_cap_min=60),
        assignee=executor,
        owner="u_yang",
        state=state,
        priority=Priority.P1,
        trace_id="trace_test",
        decision_gate=decision_gate,
    )


class AdapterRegistry(unittest.TestCase):
    def test_three_default_adapters_registered(self):
        self.assertIn("research", REGISTRY)
        self.assertIn("drafting", REGISTRY)
        self.assertIn("analysis", REGISTRY)

    def test_resolve_returns_correct_adapter(self):
        wi = _make_workitem(kind="drafting")
        self.assertIsInstance(resolve(wi), DraftingAdapter)

    def test_resolve_missing_kind_raises(self):
        wi = _make_workitem(kind=None)
        with self.assertRaises(KeyError):
            resolve(wi)

    def test_resolve_unknown_kind_raises(self):
        wi = _make_workitem(kind="not_real")
        with self.assertRaises(KeyError):
            resolve(wi)


class AdapterBehavior(unittest.TestCase):
    def test_research_emits_table_artifact_and_submit(self):
        wi = _make_workitem(state=WorkitemState.IN_PROGRESS, kind="research")
        result = ResearchAdapter().run(wi)
        self.assertEqual(result.next_trigger, "submit")
        self.assertEqual(result.artifacts[0].type, "table")
        self.assertGreater(result.tokens_used, 0)

    def test_drafting_emits_doc_artifact(self):
        wi = _make_workitem(state=WorkitemState.IN_PROGRESS, kind="drafting")
        result = DraftingAdapter().run(wi)
        self.assertEqual(result.artifacts[0].type, "doc")

    def test_analysis_with_decision_gate_routes_to_request_decision(self):
        wi = _make_workitem(
            state=WorkitemState.IN_PROGRESS, kind="analysis", decision_gate=True
        )
        result = AnalysisAdapter().run(wi)
        self.assertEqual(result.next_trigger, "request_decision")
        self.assertIsNotNone(wi.decision)
        self.assertGreaterEqual(len(wi.decision.options), 2)

    def test_analysis_without_decision_gate_routes_to_submit(self):
        wi = _make_workitem(state=WorkitemState.IN_PROGRESS, kind="analysis")
        result = AnalysisAdapter().run(wi)
        self.assertEqual(result.next_trigger, "submit")
        self.assertIsNone(wi.decision)


class RunnerPlan(unittest.TestCase):
    def test_plan_marks_needs_start_when_queued(self):
        wi = _make_workitem(state=WorkitemState.QUEUED, kind="research")
        plan = run_workitem_plan(wi)
        self.assertTrue(plan["needs_start"])
        self.assertIsInstance(plan["adapter"], ResearchAdapter)

    def test_plan_does_not_need_start_when_in_progress(self):
        wi = _make_workitem(state=WorkitemState.IN_PROGRESS, kind="research")
        plan = run_workitem_plan(wi)
        self.assertFalse(plan["needs_start"])

    def test_plan_blocked_for_terminal_state(self):
        wi = _make_workitem(state=WorkitemState.APPROVED, kind="research")
        with self.assertRaises(StateTransitionError):
            run_workitem_plan(wi)


class DecisionLoop(unittest.TestCase):
    """端到端：run-agent → request_decision → decide → 续跑 → submit。"""

    def test_decide_triggers_followup_run_to_submit(self):
        from backend.app.repository import InMemoryStore

        store = InMemoryStore()
        wi_id = "wi_gap_analysis"
        # 预设状态机要求：节点已 in_progress 才能 request_decision
        store.workitems[wi_id].state = WorkitemState.IN_PROGRESS

        first = store.run_agent(wi_id, actor="u_yang")
        self.assertEqual(first["agent"], "analysis")
        self.assertEqual(first["agent_result"]["next_trigger"], "request_decision")
        wi = store.workitems[wi_id]
        self.assertEqual(wi.state, WorkitemState.AWAITING_DECISION)
        self.assertIsNotNone(wi.decision)
        self.assertGreaterEqual(len(wi.decision.options), 2)

        # 阳哥在前端选了"继续推进"
        chosen = next(o for o in wi.decision.options if "继续" in o)
        decide_result = store.apply_action(
            wi_id,
            "decide",
            actor="u_yang",
            payload={"decision": chosen, "reasoning": "evidence is sufficient"},
        )

        # decide 之后自动续跑了 analysis adapter，进入 submitted
        self.assertEqual(wi.decision.selected_option, chosen)
        self.assertEqual(wi.decision.reasoning, "evidence is sufficient")
        self.assertIn("followup", decide_result)
        self.assertEqual(decide_result["followup"]["agent_result"]["next_trigger"], "submit")
        self.assertEqual(wi.state, WorkitemState.SUBMITTED)

    def test_decide_terminate_routes_to_reject(self):
        from backend.app.repository import InMemoryStore

        store = InMemoryStore()
        wi_id = "wi_gap_analysis"
        store.workitems[wi_id].state = WorkitemState.IN_PROGRESS
        store.run_agent(wi_id, actor="u_yang")
        wi = store.workitems[wi_id]
        terminate = next(o for o in wi.decision.options if "终止" in o)

        result = store.apply_action(
            wi_id,
            "decide",
            actor="u_yang",
            payload={"decision": terminate, "reasoning": "not viable"},
        )
        self.assertEqual(wi.state, WorkitemState.CANCELLED)
        self.assertEqual(result["followup"]["agent_result"]["next_trigger"], "cancel")


class IdempotencyGuard(unittest.TestCase):
    """AGT-02：run_agent 幂等保护。"""

    def test_run_agent_rejects_when_awaiting_decision(self):
        from backend.app.repository import IdempotencyConflict, InMemoryStore

        store = InMemoryStore()
        wi_id = "wi_gap_analysis"
        # 默认就是 awaiting_decision，应直接拒绝
        with self.assertRaises(IdempotencyConflict):
            store.run_agent(wi_id, actor="u_yang")

    def test_run_agent_rejects_when_terminal(self):
        from backend.app.repository import IdempotencyConflict, InMemoryStore

        store = InMemoryStore()
        wi_id = "wi_collect_competitors"
        store.workitems[wi_id].state = WorkitemState.APPROVED
        with self.assertRaises(IdempotencyConflict):
            store.run_agent(wi_id, actor="u_yang")

    def test_run_agent_rejects_concurrent_call(self):
        """通过手工占位 _inflight 模拟连点：第二次调用应被锁挡住。"""
        from backend.app.repository import IdempotencyConflict, InMemoryStore

        store = InMemoryStore()
        wi_id = "wi_collect_competitors"
        # 把锁手动占住，模拟另一个请求正在跑
        store._inflight.add((wi_id, "run_agent"))
        try:
            with self.assertRaises(IdempotencyConflict):
                store.run_agent(wi_id, actor="u_yang")
        finally:
            store._inflight.discard((wi_id, "run_agent"))

        # 锁释放后可以正常跑
        result = store.run_agent(wi_id, actor="u_yang")
        self.assertEqual(result["agent"], "research")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

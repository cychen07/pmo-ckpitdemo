"""OBJ-01 + OBJ-02 单测：覆盖工作项与工作流状态机的核心规则。"""
import unittest

from backend.app.domain.models import (
    AcceptanceCriterion,
    Budget,
    Capability,
    DecisionGate,
    Executor,
    ExecutorType,
    Priority,
    Workflow,
    WorkflowEdge,
    WorkflowState,
    Workitem,
    WorkitemState,
)
from backend.app.domain.state_machine import (
    StateTransitionError,
    can_transition,
    transition,
    transition_workflow,
)


def make_executor() -> Executor:
    return Executor(
        id="agent_test",
        name="Agent Test",
        type=ExecutorType.AGENT,
        capabilities=[Capability("测试", 0.9)],
        current_load=0,
        unit_cost=0.1,
        success_rate=0.9,
        rework_rate=0.1,
        owner_user_id="u_owner",
        agent_spec={"adapter": "openai"},
    )


def make_item(
    state: WorkitemState = WorkitemState.QUEUED,
    *,
    checked: bool = False,
    decision_gate: bool = False,
    budget: Budget | None = None,
    owner: str = "u_owner",
) -> Workitem:
    return Workitem(
        id="wi_test",
        title="测试工作项",
        goal="验证状态机",
        inputs=[],
        expected_outputs=[],
        acceptance_criteria=[AcceptanceCriterion("ac", "完成", checked)],
        tool_whitelist=["unit_test"],
        budget=budget or Budget(100, 1, 10),
        assignee=make_executor(),
        owner=owner,
        state=state,
        priority=Priority.P1,
        trace_id="trace_test",
        decision_gate=decision_gate,
        decision=DecisionGate(
            id="dg",
            title="测试决策",
            owner=owner,
            sla_at="2026-12-31T00:00:00Z",
            options=["A", "B", "C"],
        )
        if decision_gate
        else None,
    )


def make_workflow(state: WorkflowState = WorkflowState.RUNNING) -> Workflow:
    return Workflow(
        id="wf_test",
        title="测试工作流",
        nodes=["wi_test"],
        edges=[WorkflowEdge("a", "b")],
        template_id=None,
        sla="P1D",
        rollback_policy="retry",
        state=state,
    )


class WorkitemHappyPath(unittest.TestCase):
    def test_assign_starts_queued_workitem(self):
        item = make_item()
        result = transition(item, "assign", "u_owner")
        self.assertEqual(result.from_state, WorkitemState.QUEUED)
        self.assertEqual(result.to_state, WorkitemState.IN_PROGRESS)
        self.assertIn("trace.start", result.side_effects)
        self.assertIsNotNone(item.started_at)
        self.assertIn("workitem.assign", result.audit.action)
        self.assertEqual(result.audit.from_state, "queued")
        self.assertEqual(result.audit.to_state, "in_progress")

    def test_pause_then_resume(self):
        item = make_item(WorkitemState.IN_PROGRESS)
        transition(item, "pause", "u_owner")
        self.assertEqual(item.state, WorkitemState.PAUSED)
        result = transition(item, "resume", "u_owner")
        self.assertEqual(result.to_state, WorkitemState.IN_PROGRESS)

    def test_request_decision_then_decide(self):
        item = make_item(WorkitemState.IN_PROGRESS, decision_gate=True)
        transition(item, "request_decision", "u_owner")
        self.assertEqual(item.state, WorkitemState.AWAITING_DECISION)
        result = transition(
            item, "decide", "u_owner", {"decision": "B", "reasoning": "覆盖度高"}
        )
        self.assertEqual(result.to_state, WorkitemState.IN_PROGRESS)
        self.assertEqual(item.decision.selected_option, "B")
        self.assertEqual(item.decision.reasoning, "覆盖度高")

    def test_full_acceptance_flow(self):
        item = make_item(WorkitemState.IN_PROGRESS, checked=True)
        transition(item, "submit", "u_owner")
        result = transition(item, "approve", "u_owner")
        self.assertEqual(result.to_state, WorkitemState.APPROVED)
        self.assertIsNotNone(item.completed_at)
        self.assertTrue(item.is_terminal)


class WorkitemGuards(unittest.TestCase):
    def test_reject_requires_reason(self):
        item = make_item(WorkitemState.SUBMITTED)
        with self.assertRaises(StateTransitionError):
            transition(item, "reject", "u_owner")

    def test_reject_records_history(self):
        item = make_item(WorkitemState.SUBMITTED)
        transition(item, "reject", "u_owner", {"reason": "缺数据"})
        self.assertEqual(item.state, WorkitemState.REJECTED)
        self.assertEqual(len(item.rejection_history), 1)
        self.assertEqual(item.rejection_history[0]["reason"], "缺数据")

    def test_approve_requires_full_acceptance(self):
        item = make_item(WorkitemState.SUBMITTED, checked=False)
        with self.assertRaises(StateTransitionError):
            transition(item, "approve", "u_owner")

    def test_approve_requires_owner(self):
        item = make_item(WorkitemState.SUBMITTED, checked=True, owner="u_owner")
        with self.assertRaises(StateTransitionError):
            transition(item, "approve", "u_other")
        # 强制 force=True 时允许跳过 owner 校验（管理员行为）
        result = transition(item, "approve", "u_other", {"force": True})
        self.assertEqual(result.to_state, WorkitemState.APPROVED)

    def test_decision_gate_requires_decision_payload(self):
        item = make_item(WorkitemState.AWAITING_DECISION, decision_gate=True)
        with self.assertRaises(StateTransitionError):
            transition(item, "decide", "u_owner")

    def test_takeover_requires_audit_trail(self):
        item = make_item(WorkitemState.IN_PROGRESS)
        with self.assertRaises(StateTransitionError):
            transition(item, "takeover", "u_owner")
        result = transition(
            item, "takeover", "u_owner", {"new_owner": "u_lin", "note": "上手"}
        )
        self.assertEqual(result.to_state, WorkitemState.IN_PROGRESS)
        self.assertEqual(item.owner, "u_lin")

    def test_submit_blocked_by_exhausted_budget(self):
        budget = Budget(100, 1, 10, tokens_used=200)
        item = make_item(WorkitemState.IN_PROGRESS, budget=budget)
        with self.assertRaises(StateTransitionError):
            transition(item, "submit", "u_owner")
        # 带 override 标记可放行
        result = transition(item, "submit", "u_owner", {"override_budget": True})
        self.assertEqual(result.to_state, WorkitemState.SUBMITTED)


class WorkitemIllegalTransitions(unittest.TestCase):
    def test_cannot_assign_in_progress(self):
        item = make_item(WorkitemState.IN_PROGRESS)
        with self.assertRaises(StateTransitionError):
            transition(item, "assign", "u_owner")

    def test_cannot_cancel_terminal(self):
        item = make_item(WorkitemState.APPROVED, checked=True)
        with self.assertRaises(StateTransitionError):
            transition(item, "cancel", "u_owner")

    def test_unsupported_trigger_rejected(self):
        item = make_item()
        with self.assertRaises(StateTransitionError):
            transition(item, "explode", "u_owner")

    def test_can_transition_helper(self):
        item = make_item(WorkitemState.IN_PROGRESS)
        self.assertTrue(can_transition(item, "submit"))
        self.assertFalse(can_transition(item, "approve"))


class WorkflowStateMachine(unittest.TestCase):
    def test_pause_resume(self):
        wf = make_workflow(WorkflowState.RUNNING)
        event = transition_workflow(wf, "pause", "u_owner")
        self.assertEqual(wf.state, WorkflowState.PAUSED)
        self.assertEqual(event.to_state, "paused")
        transition_workflow(wf, "resume", "u_owner")
        self.assertEqual(wf.state, WorkflowState.RUNNING)

    def test_complete_only_from_running(self):
        wf = make_workflow(WorkflowState.PAUSED)
        with self.assertRaises(StateTransitionError):
            transition_workflow(wf, "complete", "u_owner")

    def test_cancel_blocks_after_terminal(self):
        wf = make_workflow(WorkflowState.RUNNING)
        transition_workflow(wf, "complete", "u_owner")
        with self.assertRaises(StateTransitionError):
            transition_workflow(wf, "cancel", "u_owner")


class AuditAndEvents(unittest.TestCase):
    def test_audit_event_payload_preserved(self):
        item = make_item(WorkitemState.IN_PROGRESS, checked=True)
        result = transition(item, "submit", "u_owner", {"note": "ready"})
        self.assertEqual(result.audit.payload, {"note": "ready"})
        self.assertIn("workitem.submitted", result.domain_events)

    def test_decide_emits_decision_resolved(self):
        item = make_item(WorkitemState.AWAITING_DECISION, decision_gate=True)
        result = transition(item, "decide", "u_owner", {"decision": "A"})
        self.assertIn("decision.resolved", result.domain_events)
        self.assertIn("trace.decision:A", result.side_effects)


if __name__ == "__main__":
    unittest.main()

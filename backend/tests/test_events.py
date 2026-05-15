"""EventBus 与 SSE 集成的 smoke 测试。"""
from __future__ import annotations

import asyncio
import json
import unittest

from backend.app.events import EventBus, bus
from backend.app.repository import InMemoryStore


class EventBusUnit(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus(buffer=10)

    def test_publish_increments_seq_and_history(self):
        e1 = self.bus.publish("a")
        e2 = self.bus.publish("b")
        self.assertEqual(e1.seq, 1)
        self.assertEqual(e2.seq, 2)
        self.assertEqual([e.name for e in self.bus.history()], ["a", "b"])

    def test_history_buffer_caps_at_capacity(self):
        for i in range(15):
            self.bus.publish(f"e{i}")
        history = self.bus.history(limit=20)
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0].name, "e5")

    def test_publish_with_no_subscribers_does_not_raise(self):
        self.bus.publish("solo")  # 不应抛异常
        self.assertEqual(self.bus.subscriber_count(), 0)


class EventBusSubscribe(unittest.IsolatedAsyncioTestCase):
    async def test_subscriber_receives_published_events(self):
        b = EventBus()

        received: list[str] = []

        async def consumer():
            async for event in b.subscribe():
                received.append(event.name)
                if len(received) == 2:
                    break

        task = asyncio.create_task(consumer())
        # 给 consumer 一点时间挂上去
        await asyncio.sleep(0.01)
        b.publish("first")
        b.publish("second")
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(received, ["first", "second"])

    async def test_replay_delivers_recent_history_first(self):
        b = EventBus()
        b.publish("h1")
        b.publish("h2")

        received: list[str] = []

        async def consumer():
            async for event in b.subscribe(replay=2):
                received.append(event.name)
                if len(received) == 3:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        b.publish("live")
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(received, ["h1", "h2", "live"])


class RepositoryToBus(unittest.TestCase):
    def setUp(self):
        bus.reset()
        self.store = InMemoryStore()

    def test_apply_action_publishes_domain_events(self):
        bus.reset()
        self.store.apply_action("wi_collect_competitors", "submit", "u_yang")
        names = [e.name for e in bus.history()]
        self.assertIn("workitem.submit", names)
        self.assertIn("workitem.submitted", names)

    def test_run_agent_publishes_completion_and_state_change(self):
        bus.reset()
        result = self.store.run_agent("wi_draft_report", actor="system")
        names = [e.name for e in bus.history()]
        self.assertIn("agent.drafting.completed", names)
        self.assertIn("workitem.submit", names)
        self.assertEqual(result["agent"], "drafting")
        # 预算与 artifacts 应被回写
        wi = self.store.workitems["wi_draft_report"]
        self.assertGreater(wi.budget.tokens_used, 0)
        self.assertEqual(len(wi.artifacts), 1)


class SSEFormatting(unittest.IsolatedAsyncioTestCase):
    async def test_sse_payload_is_json_parseable(self):
        b = EventBus()
        b.publish("workitem.submit", workitem_id="wi_1", payload={"ok": True})

        async for event in b.subscribe(replay=1):
            line = (
                f"id: {event.seq}\n"
                f"event: {event.name}\n"
                f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"
            )
            data_line = [l for l in line.split("\n") if l.startswith("data: ")][0]
            decoded = json.loads(data_line[len("data: "):])
            self.assertEqual(decoded["name"], "workitem.submit")
            self.assertEqual(decoded["workitem_id"], "wi_1")
            break


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

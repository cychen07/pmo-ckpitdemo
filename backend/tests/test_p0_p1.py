"""P0/P1 新增功能单测：BUD-01 / AUTH-01 / TPL-01 / PER-01 / RES-01。"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.app.auth import TOKEN_TABLE, require_permission
from backend.app.main import app
from backend.app.repository import InMemoryStore


class BudgetGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.workitem = self.store.workitems["wi_collect_competitors"]

    def test_check_budget_warning_at_80pct(self) -> None:
        b = self.workitem.budget
        b.tokens_used = int(b.token_cap * 0.85)
        self.assertEqual(self.store._check_budget(self.workitem), "warning")

    def test_check_budget_exhausted_over_100pct(self) -> None:
        b = self.workitem.budget
        b.tokens_used = b.token_cap + 1
        self.assertEqual(self.store._check_budget(self.workitem), "exhausted")

    def test_check_budget_none_when_low(self) -> None:
        self.assertIsNone(self.store._check_budget(self.workitem))


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_whoami_without_token_returns_401(self) -> None:
        resp = self.client.get("/v1/whoami")
        self.assertEqual(resp.status_code, 401)

    def test_whoami_with_operator_token(self) -> None:
        resp = self.client.get(
            "/v1/whoami", headers={"Authorization": "Bearer demo-operator"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "operator")

    def test_invalid_token_returns_401(self) -> None:
        resp = self.client.get(
            "/v1/whoami", headers={"Authorization": "Bearer bogus-token"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_operator_forbidden_to_approve(self) -> None:
        # operator 无 approve 权限 → 403
        resp = self.client.post(
            "/v1/workitems/wi_collect_competitors/approve",
            json={"payload": {}},
            headers={"Authorization": "Bearer demo-operator"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_require_permission_allows_matching_role(self) -> None:
        dep = require_permission("run_agent")
        user = TOKEN_TABLE["demo-operator"]
        # Depends 内部函数直接调用
        self.assertEqual(dep(user=user), user)


class TemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()

    def test_seed_templates_registers_competitive_analysis(self) -> None:
        tpls = self.store.list_templates()
        ids = [t["id"] for t in tpls]
        self.assertIn("tpl_competitive_analysis", ids)

    def test_create_and_delete_template(self) -> None:
        tpl = self.store.create_template(
            {
                "title": "Demo",
                "description": "d",
                "nodes": [
                    {
                        "role": "research",
                        "title": "R",
                        "goal": "do",
                        "acceptance_criteria": [{"label": "x", "checked": False}],
                        "budget": {"token_cap": 100, "cost_cap_usd": 1, "time_cap_min": 5},
                    }
                ],
                "edges": [],
            }
        )
        self.assertIn(tpl["id"], self.store.templates)
        self.store.delete_template(tpl["id"])
        self.assertNotIn(tpl["id"], self.store.templates)

    def test_instantiate_template_creates_workflow_and_workitems(self) -> None:
        wf = self.store.instantiate_template("tpl_competitive_analysis", title="clone")
        self.assertEqual(wf["title"], "clone")
        # 3 个模板节点 → 3 个 workitem
        self.assertEqual(len(wf["nodes"]), 3)
        # 首节点应为 in_progress
        self.assertEqual(wf["nodes"][0]["state"], "in_progress")
        # 末节点应是决策门
        self.assertTrue(wf["nodes"][-1]["decision_gate"])


class PersistenceTests(unittest.TestCase):
    def test_save_and_load_roundtrip_templates(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.json"
            s1 = InMemoryStore(snapshot_path=path)
            s1.create_template(
                {
                    "id": "tpl_custom",
                    "title": "MyTpl",
                    "description": "",
                    "nodes": [],
                    "edges": [],
                }
            )
            # create_template 自动 _persist
            self.assertTrue(path.exists())

            s2 = InMemoryStore(snapshot_path=path)
            s2.load_snapshot()
            self.assertIn("tpl_custom", s2.templates)

    def test_save_and_load_roundtrip_business_state(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "snap.json"
            s1 = InMemoryStore(snapshot_path=path)
            wi = s1.workitems["wi_collect_competitors"]
            wi.budget.tokens_used = 1234
            wi.artifacts.append(
                {
                    "id": "bad"
                }  # type: ignore[arg-type]
            )
            wi.artifacts.clear()
            from backend.app.domain.models import Artifact
            wi.artifacts.append(
                Artifact(
                    id="art_1",
                    workitem_id=wi.id,
                    type="doc",
                    title="draft",
                    uri="memory://draft",
                    version=2,
                )
            )
            s1.save_snapshot()

            s2 = InMemoryStore(snapshot_path=path)
            self.assertTrue(s2.load_snapshot())
            self.assertIn("wf_competitive_analysis", s2.workflows)
            self.assertEqual(s2.workitems["wi_collect_competitors"].budget.tokens_used, 1234)
            self.assertEqual(len(s2.workitems["wi_collect_competitors"].artifacts), 1)
            self.assertIn("trace_collect_competitors", s2.traces)

    def test_save_snapshot_without_path_raises(self) -> None:
        s = InMemoryStore()
        with self.assertRaises(ValueError):
            s.save_snapshot()

    def test_postgres_mode_without_dsn_raises(self) -> None:
        s = InMemoryStore()
        s.configure_persistence("./data/store.json", mode="postgres")
        with self.assertRaises(ValueError):
            s.save_snapshot()


class ResourceRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()

    def test_recommend_returns_match_score_and_reasons(self) -> None:
        result = self.store.recommend_executors("报告起草")
        self.assertTrue(result)
        top = result[0]
        self.assertIn("match_score", top)
        self.assertIn("composite_score", top)
        self.assertIn("recommend_reasons", top)
        self.assertGreater(top["match_score"], 0)

    def test_recommend_without_capability_still_ranks(self) -> None:
        result = self.store.recommend_executors()
        self.assertTrue(result)
        self.assertIn("composite_score", result[0])


if __name__ == "__main__":
    unittest.main()

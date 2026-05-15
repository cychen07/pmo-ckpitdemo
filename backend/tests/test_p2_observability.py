"""P2: OBS-01 / WF-CTL 单测。"""
from __future__ import annotations

from dataclasses import replace
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.alerting import alert_router
from backend.app.events import bus
from backend.app.jobs import job_manager
from backend.app.main import app
from backend.app.metrics import aggregate_metrics, list_alerts, workflow_overview
from backend.app.runtime import RuntimeSettings, runtime_settings as main_runtime_settings
from backend.app.repository import InMemoryStore, store
from backend.app.tasking import tasking_service


class ObservabilityMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()
        alert_router.reset()
        self.store = InMemoryStore()

    def test_aggregate_metrics_contains_summary_budget_and_agent_costs(self) -> None:
        wi = self.store.workitems["wi_collect_competitors"]
        wi.budget.tokens_used = 8_000
        wi.budget.cost_used_usd = 3.2

        metrics = aggregate_metrics(self.store)

        self.assertEqual(metrics["summary"]["workitems_total"], 3)
        self.assertIn("budget", metrics)
        self.assertGreater(metrics["budget"]["tokens"]["pct"], 0)
        self.assertTrue(metrics["agent_costs"])

    def test_alerts_include_budget_events(self) -> None:
        bus.publish(
            "budget.warning",
            workitem_id="wi_collect_competitors",
            workflow_id="wf_competitive_analysis",
            payload={"reason": "token budget above 80%"},
        )

        alerts = list_alerts(self.store)

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["name"], "budget.warning")
        self.assertEqual(alerts[0]["workitem_title"], "竞品信息收集")

    def test_workflow_overview_returns_progress_and_allowed_actions(self) -> None:
        overview = workflow_overview(self.store, "wf_competitive_analysis")

        self.assertEqual(overview["id"], "wf_competitive_analysis")
        self.assertEqual(overview["total_nodes"], 3)
        self.assertIn("progress_pct", overview)
        self.assertIn("pause", overview["allowed_actions"])


class ObservabilityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        alert_router.reset()
        bus.reset()
        job_manager.reset()
        store.ops_tasks.clear()
        store.audit_log.clear()
        tasking_service.lark_api._tenant_token = None  # noqa: SLF001
        tasking_service.lark_api._token_expire_at = 0  # noqa: SLF001
        job_manager.start()
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer demo-owner"}

    def tearDown(self) -> None:
        alert_router.reset()
        job_manager.reset()
        store.ops_tasks.clear()
        tasking_service.lark_api._tenant_token = None  # noqa: SLF001
        tasking_service.lark_api._token_expire_at = 0  # noqa: SLF001

    def test_metrics_endpoint(self) -> None:
        resp = self.client.get("/v1/metrics", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("summary", resp.json())

    def test_workflow_overview_endpoint(self) -> None:
        resp = self.client.get(
            "/v1/workflows/wf_competitive_analysis/overview",
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], "wf_competitive_analysis")

    def test_operator_cannot_cancel_workflow(self) -> None:
        resp = self.client.post(
            "/v1/workflows/wf_competitive_analysis/cancel",
            json={"payload": {}},
            headers={"Authorization": "Bearer demo-operator"},
        )

        self.assertEqual(resp.status_code, 403)

    def test_events_history_accepts_query_token_and_rejects_anonymous(self) -> None:
        denied = self.client.get("/v1/events/history")
        allowed = self.client.get("/v1/events/history?access_token=demo-owner")

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)

    def test_artifact_content_endpoint_returns_stored_payload(self) -> None:
        run_resp = self.client.post(
            "/v1/workitems/wi_collect_competitors/run-agent",
            json={"payload": {}},
            headers=self.headers,
        )
        self.assertEqual(run_resp.status_code, 200)
        self.assertEqual(run_resp.json()["runtime"]["execution_mode"], "inline")
        workflow = self.client.get(
            "/v1/workflows/wf_competitive_analysis",
            headers=self.headers,
        ).json()
        artifact_id = workflow["nodes"][0]["artifacts"][0]["id"]

        resp = self.client.get(
            f"/v1/artifacts/{artifact_id}/content",
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["artifact"]["id"], artifact_id)
        self.assertIn("content", resp.json())
        self.assertEqual(resp.json()["artifact"]["external_refs"][0]["kind"], "local_artifact")

    def test_create_workflow_endpoint_creates_draft_workflow(self) -> None:
        resp = self.client.post(
            "/v1/workflows",
            json={"title": "Prod Workflow"},
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Prod Workflow")
        self.assertEqual(resp.json()["state"], "draft")

    def test_runtime_endpoint_exposes_modes(self) -> None:
        resp = self.client.get("/v1/runtime", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["delivery_provider"], "local")
        self.assertEqual(resp.json()["execution_mode"], "inline")
        self.assertEqual(resp.json()["job_stats"]["provider"], "memory")
        self.assertIn("job_timeout_sec", resp.json())
        self.assertIn("delivery_health", resp.json())

    def test_runtime_health_endpoint_exposes_queue_and_store(self) -> None:
        resp = self.client.get("/v1/runtime/health", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["queue"]["ok"])
        self.assertEqual(resp.json()["state_store"]["provider"], "json_snapshot")
        self.assertIn("delivery", resp.json())

    def test_readyz_returns_200_for_default_local_runtime(self) -> None:
        resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")

    def test_runtime_health_reports_missing_postgres_dsn(self) -> None:
        postgres_settings = RuntimeSettings(
            delivery_provider=main_runtime_settings.delivery_provider,
            notification_provider=main_runtime_settings.notification_provider,
            execution_mode=main_runtime_settings.execution_mode,
            state_store_mode="postgres",
            object_store_mode=main_runtime_settings.object_store_mode,
            queue_provider=main_runtime_settings.queue_provider,
            redis_url=main_runtime_settings.redis_url,
            postgres_dsn=None,
            snapshot_key=main_runtime_settings.snapshot_key,
            lark_base_url=main_runtime_settings.lark_base_url,
            lark_doc_folder_token=main_runtime_settings.lark_doc_folder_token,
            lark_app_id=main_runtime_settings.lark_app_id,
            lark_app_secret=main_runtime_settings.lark_app_secret,
            lark_bot_webhook_url=main_runtime_settings.lark_bot_webhook_url,
        )
        with patch("backend.app.main.runtime_settings", postgres_settings):
            resp = self.client.get("/v1/runtime/health", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["state_store"]["ok"])

    def test_readyz_returns_503_when_postgres_required_but_missing(self) -> None:
        postgres_settings = RuntimeSettings(
            delivery_provider=main_runtime_settings.delivery_provider,
            notification_provider=main_runtime_settings.notification_provider,
            execution_mode=main_runtime_settings.execution_mode,
            state_store_mode="postgres",
            object_store_mode=main_runtime_settings.object_store_mode,
            queue_provider=main_runtime_settings.queue_provider,
            redis_url=main_runtime_settings.redis_url,
            postgres_dsn=None,
            snapshot_key=main_runtime_settings.snapshot_key,
            startup_strict=False,
            lark_base_url=main_runtime_settings.lark_base_url,
            lark_doc_folder_token=main_runtime_settings.lark_doc_folder_token,
            lark_app_id=main_runtime_settings.lark_app_id,
            lark_app_secret=main_runtime_settings.lark_app_secret,
            lark_bot_webhook_url=main_runtime_settings.lark_bot_webhook_url,
        )
        with patch("backend.app.main.runtime_settings", postgres_settings):
            resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "degraded")

    def test_readyz_returns_503_when_redis_required_but_not_configured(self) -> None:
        redis_settings = RuntimeSettings(
            delivery_provider=main_runtime_settings.delivery_provider,
            notification_provider=main_runtime_settings.notification_provider,
            execution_mode="queued",
            state_store_mode=main_runtime_settings.state_store_mode,
            object_store_mode=main_runtime_settings.object_store_mode,
            queue_provider="redis",
            redis_url=None,
            postgres_dsn=main_runtime_settings.postgres_dsn,
            snapshot_key=main_runtime_settings.snapshot_key,
            startup_strict=False,
            lark_base_url=main_runtime_settings.lark_base_url,
            lark_doc_folder_token=main_runtime_settings.lark_doc_folder_token,
            lark_app_id=main_runtime_settings.lark_app_id,
            lark_app_secret=main_runtime_settings.lark_app_secret,
            lark_bot_webhook_url=main_runtime_settings.lark_bot_webhook_url,
        )
        with patch("backend.app.main.runtime_settings", redis_settings):
            resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "degraded")
        self.assertFalse(resp.json()["queue"]["ok"])

    def test_runtime_health_reports_lark_delivery_misconfigured(self) -> None:
        lark_settings = replace(
            main_runtime_settings,
            delivery_provider="lark",
            notification_provider="lark_webhook",
            lark_app_id=None,
            lark_app_secret=None,
            lark_bot_webhook_url=None,
        )
        with patch("backend.app.main.runtime_settings", lark_settings):
            resp = self.client.get("/v1/runtime/health", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["delivery"]["artifact_delivery"]["ok"])
        self.assertFalse(resp.json()["delivery"]["notification"]["ok"])
        self.assertFalse(resp.json()["ready"])

    def test_readyz_preflights_lark_task_api_credentials(self) -> None:
        settings = replace(
            main_runtime_settings,
            ops_task_provider="lark_task_api",
            lark_app_id="cli_xxx",
            lark_app_secret="invalid_secret",
        )
        tasking_service.lark_api._tenant_token = None  # noqa: SLF001
        tasking_service.lark_api._token_expire_at = 0  # noqa: SLF001
        with (
            patch("backend.app.main.runtime_settings", settings),
            patch("backend.app.tasking._http_json", return_value={"code": 10014, "msg": "app secret invalid"}),
        ):
            resp = self.client.get("/readyz")

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "degraded")
        self.assertFalse(resp.json()["tasking"]["ok"])
        self.assertEqual(resp.json()["tasking"]["mode"], "auth_failed")
        self.assertEqual(resp.json()["tasking"]["lark_error_code"], 10014)
        self.assertEqual(resp.json()["tasking"]["lark_error_msg"], "app secret invalid")

    def test_alert_outbox_contains_routed_alert_event(self) -> None:
        bus.publish(
            "job.dead_lettered",
            workitem_id="wi_collect_competitors",
            payload={"error": "timed out"},
        )
        resp = self.client.get("/v1/alerts/outbox", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["event_name"], "job.dead_lettered")
        self.assertIn("timed out", resp.json()[0]["summary"])
        self.assertEqual(resp.json()[0]["attempts"], 1)

    def test_alert_outbox_resend_keeps_delivery_state(self) -> None:
        bus.publish(
            "budget.warning",
            workitem_id="wi_collect_competitors",
            payload={"tokens_pct": 81},
        )
        listed = self.client.get("/v1/alerts/outbox", headers=self.headers)
        alert_id = listed.json()[0]["id"]
        resent = self.client.post(f"/v1/alerts/outbox/{alert_id}/resend", headers=self.headers)

        self.assertEqual(resent.status_code, 200)
        self.assertEqual(resent.json()["status"], "buffered")
        self.assertEqual(resent.json()["attempts"], 2)

    def test_alert_outbox_can_filter_by_status(self) -> None:
        bus.publish(
            "budget.warning",
            workitem_id="wi_collect_competitors",
            payload={"tokens_pct": 81},
        )
        resp = self.client.get("/v1/alerts/outbox?status=buffered", headers=self.headers)

        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 1)
        self.assertTrue(all(item["status"] == "buffered" for item in resp.json()))

    def test_alert_outbox_batch_resend_by_status(self) -> None:
        bus.publish(
            "budget.warning",
            workitem_id="wi_collect_competitors",
            payload={"tokens_pct": 81},
        )
        bus.publish(
            "agent.followup.failed",
            workitem_id="wi_collect_competitors",
            payload={"error": "network"},
        )
        resent = self.client.post(
            "/v1/alerts/outbox/resend",
            json={"status": "buffered"},
            headers=self.headers,
        )

        self.assertEqual(resent.status_code, 200)
        self.assertEqual(len(resent.json()), 2)
        self.assertTrue(all(item["attempts"] == 2 for item in resent.json()))

    def test_alert_outbox_clusters_and_batch_resend_by_cluster(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish(
                "budget.warning",
                workitem_id="wi_collect_competitors",
                payload={"tokens_pct": 81},
            )
            bus.publish(
                "budget.warning",
                workitem_id="wi_collect_competitors",
                payload={"tokens_pct": 82},
            )
            clusters = self.client.get("/v1/alerts/outbox/clusters?status=buffered", headers=self.headers)

        self.assertEqual(clusters.status_code, 200)
        self.assertEqual(clusters.json()[0]["count"], 2)
        resent = self.client.post(
            "/v1/alerts/outbox/resend",
            json={"cluster_key": clusters.json()[0]["cluster_key"]},
            headers=self.headers,
        )
        self.assertEqual(resent.status_code, 200)
        self.assertEqual(len(resent.json()), 2)
        self.assertTrue(all(item["attempts"] == 2 for item in resent.json()))

    def test_alert_outbox_dedup_suppresses_duplicates(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=60, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 82})

        listed = self.client.get("/v1/alerts/outbox", headers=self.headers)
        runtime = self.client.get("/v1/runtime", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertGreaterEqual(runtime.json()["alerting_stats"]["suppressed_total"], 1)

    def test_alert_outbox_can_silence_cluster(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            clusters = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
            cluster_key = clusters.json()[0]["cluster_key"]
            silenced = self.client.post(
                "/v1/alerts/outbox/silence",
                json={"cluster_key": cluster_key, "duration_sec": 60},
                headers=self.headers,
            )
            self.assertEqual(silenced.status_code, 200)
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 83})

        listed = self.client.get("/v1/alerts/outbox", headers=self.headers)
        self.assertEqual(len(listed.json()), 1)
        clustered = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
        self.assertGreaterEqual(clustered.json()[0]["suppressed_count"], 1)
        self.assertIsNotNone(clustered.json()[0]["silenced_until"])

    def test_alert_outbox_lists_active_silences(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            clusters = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
            cluster_key = clusters.json()[0]["cluster_key"]
            self.client.post(
                "/v1/alerts/outbox/silence",
                json={"cluster_key": cluster_key, "duration_sec": 120},
                headers=self.headers,
            )
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 82})

        silences = self.client.get("/v1/alerts/outbox/silences", headers=self.headers)
        self.assertEqual(silences.status_code, 200)
        self.assertEqual(len(silences.json()), 1)
        self.assertEqual(silences.json()[0]["cluster_key"], cluster_key)
        self.assertEqual(silences.json()[0]["event_name"], "budget.warning")
        self.assertGreaterEqual(silences.json()[0]["suppressed_count"], 1)
        self.assertIsNotNone(silences.json()[0]["silenced_until"])

    def test_silence_cluster_creates_alert_ops_task(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            clusters = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
            cluster_key = clusters.json()[0]["cluster_key"]
            silenced = self.client.post(
                "/v1/alerts/outbox/silence",
                json={"cluster_key": cluster_key, "duration_sec": 60},
                headers=self.headers,
            )

        self.assertEqual(silenced.status_code, 200)
        self.assertIn("ops_task", silenced.json())
        self.assertEqual(silenced.json()["ops_task"]["source_kind"], "alert.silence")
        listed = self.client.get("/v1/ops/tasks?source_kind=alert.silence", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["source_ref"], cluster_key)

    def test_alert_outbox_can_unsilence_cluster(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            clusters = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
            cluster_key = clusters.json()[0]["cluster_key"]
            self.client.post(
                "/v1/alerts/outbox/silence",
                json={"cluster_key": cluster_key, "duration_sec": 60},
                headers=self.headers,
            )
            unsilenced = self.client.post(
                "/v1/alerts/outbox/unsilence",
                json={"cluster_key": cluster_key},
                headers=self.headers,
            )
            self.assertEqual(unsilenced.status_code, 200)
            self.assertTrue(unsilenced.json()["cleared"])
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 83})

        listed = self.client.get("/v1/alerts/outbox", headers=self.headers)
        self.assertEqual(len(listed.json()), 2)

    def test_resend_alert_creates_alert_ops_task(self) -> None:
        bus.publish(
            "budget.warning",
            workitem_id="wi_collect_competitors",
            payload={"tokens_pct": 81},
        )
        listed = self.client.get("/v1/alerts/outbox", headers=self.headers)
        alert_id = listed.json()[0]["id"]
        resent = self.client.post(f"/v1/alerts/outbox/{alert_id}/resend", headers=self.headers)

        self.assertEqual(resent.status_code, 200)
        self.assertIn("ops_task", resent.json())
        self.assertEqual(resent.json()["ops_task"]["source_kind"], "alert.resend")
        tasks = self.client.get("/v1/ops/tasks?source_kind=alert.resend", headers=self.headers)
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(len(tasks.json()), 1)
        self.assertEqual(tasks.json()[0]["source_ref"], alert_id)

    def test_alert_outbox_can_unsilence_many_clusters(self) -> None:
        settings = replace(main_runtime_settings, alert_dedup_window_sec=0, alert_silence_default_sec=600)
        with patch("backend.app.alerting.runtime_settings", settings):
            bus.publish("budget.warning", workitem_id="wi_collect_competitors", payload={"tokens_pct": 81})
            bus.publish("job.dead_lettered", workitem_id="wi_collect_competitors", payload={"error": "timed out"})
            clusters = self.client.get("/v1/alerts/outbox/clusters", headers=self.headers)
            cluster_keys = [item["cluster_key"] for item in clusters.json()[:2]]
            for cluster_key in cluster_keys:
                self.client.post(
                    "/v1/alerts/outbox/silence",
                    json={"cluster_key": cluster_key, "duration_sec": 60},
                    headers=self.headers,
                )
            silences = self.client.get("/v1/alerts/outbox/silences", headers=self.headers)
            self.assertEqual(len(silences.json()), 2)
            unsilenced = self.client.post(
                "/v1/alerts/outbox/unsilence-many",
                json={"cluster_keys": cluster_keys},
                headers=self.headers,
            )
            self.assertEqual(unsilenced.status_code, 200)
            self.assertEqual(len(unsilenced.json()), 2)
            self.assertTrue(all(item["cleared"] for item in unsilenced.json()))

        listed = self.client.get("/v1/alerts/outbox/silences", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])

    def test_run_agent_returns_job_in_queued_mode(self) -> None:
        queued_settings = RuntimeSettings(
            delivery_provider=main_runtime_settings.delivery_provider,
            notification_provider=main_runtime_settings.notification_provider,
            execution_mode="queued",
            state_store_mode=main_runtime_settings.state_store_mode,
            object_store_mode=main_runtime_settings.object_store_mode,
            postgres_dsn=main_runtime_settings.postgres_dsn,
            snapshot_key=main_runtime_settings.snapshot_key,
            lark_base_url=main_runtime_settings.lark_base_url,
            lark_doc_folder_token=main_runtime_settings.lark_doc_folder_token,
            lark_app_id=main_runtime_settings.lark_app_id,
            lark_app_secret=main_runtime_settings.lark_app_secret,
            lark_bot_webhook_url=main_runtime_settings.lark_bot_webhook_url,
        )
        with (
            patch("backend.app.execution.runtime_settings", queued_settings),
            patch("backend.app.main.runtime_settings", queued_settings),
        ):
            created = self.client.post(
                "/v1/templates/tpl_competitive_analysis/instantiate",
                json={"title": "Queued Run Flow"},
                headers={"Authorization": "Bearer demo-admin"},
            )
            self.assertEqual(created.status_code, 200)
            workitem_id = created.json()["nodes"][0]["id"]
            resp = self.client.post(
                f"/v1/workitems/{workitem_id}/run-agent",
                json={"payload": {}},
                headers=self.headers,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["mode"], "queued")
            job_id = resp.json()["job"]["id"]

            deadline = time.time() + 1.5
            last = None
            while time.time() < deadline:
                last = self.client.get(f"/v1/jobs/{job_id}", headers=self.headers)
                if last.json()["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.05)

            self.assertIsNotNone(last)
            self.assertEqual(last.status_code, 200)
            self.assertEqual(last.json()["status"], "succeeded")

    def test_cancel_then_retry_job(self) -> None:
        queued_settings = RuntimeSettings(
            delivery_provider=main_runtime_settings.delivery_provider,
            notification_provider=main_runtime_settings.notification_provider,
            execution_mode="queued",
            state_store_mode=main_runtime_settings.state_store_mode,
            object_store_mode=main_runtime_settings.object_store_mode,
            queue_provider="memory",
            redis_url=main_runtime_settings.redis_url,
            postgres_dsn=main_runtime_settings.postgres_dsn,
            snapshot_key=main_runtime_settings.snapshot_key,
            lark_base_url=main_runtime_settings.lark_base_url,
            lark_doc_folder_token=main_runtime_settings.lark_doc_folder_token,
            lark_app_id=main_runtime_settings.lark_app_id,
            lark_app_secret=main_runtime_settings.lark_app_secret,
            lark_bot_webhook_url=main_runtime_settings.lark_bot_webhook_url,
        )
        job_manager.reset()
        with (
            patch("backend.app.execution.runtime_settings", queued_settings),
            patch("backend.app.main.runtime_settings", queued_settings),
        ):
            created = self.client.post(
                "/v1/templates/tpl_competitive_analysis/instantiate",
                json={"title": "Queued Cancel Flow"},
                headers={"Authorization": "Bearer demo-admin"},
            )
            workitem_id = created.json()["nodes"][0]["id"]
            resp = self.client.post(
                f"/v1/workitems/{workitem_id}/run-agent",
                json={"payload": {}},
                headers=self.headers,
            )
            job_id = resp.json()["job"]["id"]

            cancelled = self.client.post(f"/v1/jobs/{job_id}/cancel", headers=self.headers)
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["status"], "cancelled")

            retried = self.client.post(f"/v1/jobs/{job_id}/retry", headers=self.headers)
            self.assertEqual(retried.status_code, 200)
            self.assertEqual(retried.json()["status"], "queued")
            queued = self.client.get(f"/v1/jobs/{job_id}", headers=self.headers)
            self.assertEqual(queued.status_code, 200)
            self.assertEqual(queued.json()["status"], "queued")

    def test_job_retry_schedules_backoff_and_then_succeeds(self) -> None:
        settings = replace(
            main_runtime_settings,
            job_retry_backoff_sec=0.15,
            dead_letter_enabled=True,
        )
        attempts = {"count": 0}

        def flaky_runner(_: object) -> dict[str, object]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("boom")
            return {"ok": True}

        job_manager.reset()
        with patch("backend.app.jobs.runtime_settings", settings):
            job_manager.start()
            job = job_manager.submit_run_agent(
                workitem_id="wi_backoff",
                actor="u_test",
                payload={"__job_max_attempts": 2, "__job_retry_backoff_sec": 0.15},
                runner=flaky_runner,
            )
            time.sleep(0.03)
            queued = job_manager.get_job(job["id"])
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["last_failure_kind"], "error")
            self.assertIsNotNone(queued["next_retry_at"])

            deadline = time.time() + 1.0
            final = queued
            while time.time() < deadline:
                final = job_manager.get_job(job["id"])
                if final["status"] == "succeeded":
                    break
                time.sleep(0.03)

        self.assertEqual(final["status"], "succeeded")
        self.assertIsNone(final["next_retry_at"])
        self.assertEqual(final["attempts"], 2)

    def test_job_timeout_dead_letters_and_endpoint_lists_it(self) -> None:
        settings = replace(
            main_runtime_settings,
            job_retry_backoff_sec=0.01,
            dead_letter_enabled=True,
        )

        def slow_runner(_: object) -> dict[str, object]:
            time.sleep(0.05)
            return {"ok": True}

        job_manager.reset()
        with patch("backend.app.jobs.runtime_settings", settings):
            job_manager.start()
            job = job_manager.submit_run_agent(
                workitem_id="wi_timeout",
                actor="u_test",
                payload={"__job_max_attempts": 2, "__job_timeout_sec": 0.01},
                runner=slow_runner,
            )

            deadline = time.time() + 1.0
            final = job_manager.get_job(job["id"])
            while time.time() < deadline:
                final = job_manager.get_job(job["id"])
                if final["status"] == "dead_lettered":
                    break
                time.sleep(0.02)

        self.assertEqual(final["status"], "dead_lettered")
        self.assertEqual(final["last_failure_kind"], "timeout")
        self.assertIn("timed out", final["error"])
        listed = self.client.get("/v1/jobs/dead-letters", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn(job["id"], [item["id"] for item in listed.json()])

    def test_job_replay_endpoint_creates_new_job_with_overridden_payload(self) -> None:
        job_manager.reset()
        job_manager.start()

        def ok_runner(job: object) -> dict[str, object]:
            payload = getattr(job, "payload", {})
            return {"ok": True, "payload": payload}

        job = job_manager.submit_run_agent(
            workitem_id="wi_replay",
            actor="u_test",
            payload={"topic": "alpha"},
            runner=ok_runner,
        )
        deadline = time.time() + 0.5
        current = job_manager.get_job(job["id"])
        while time.time() < deadline:
            current = job_manager.get_job(job["id"])
            if current["status"] == "succeeded":
                break
            time.sleep(0.02)

        replay = self.client.post(
            f"/v1/jobs/{job['id']}/replay",
            json={"payload": {"topic": "beta", "priority": "p0"}},
            headers=self.headers,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertNotEqual(replay.json()["id"], job["id"])
        self.assertEqual(replay.json()["source_job_id"], job["id"])
        self.assertEqual(replay.json()["payload"]["topic"], "beta")
        self.assertEqual(replay.json()["payload"]["priority"], "p0")

    def test_job_replay_accepts_timeout_template_fields(self) -> None:
        job_manager.reset()
        job_manager.start()

        def ok_runner(job: object) -> dict[str, object]:
            return {"payload": getattr(job, "payload", {})}

        job = job_manager.submit_run_agent(
            workitem_id="wi_repair",
            actor="u_test",
            payload={"topic": "repair"},
            runner=ok_runner,
        )
        replay = self.client.post(
            f"/v1/jobs/{job['id']}/replay",
            json={"payload": {"__job_timeout_sec": 20, "__job_max_attempts": 4, "__job_retry_backoff_sec": 1}},
            headers=self.headers,
        )

        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["timeout_sec"], 20)
        self.assertEqual(replay.json()["max_attempts"], 4)
        self.assertEqual(replay.json()["retry_backoff_sec"], 1)

    def test_human_takeover_job_endpoint(self) -> None:
        job_manager.reset()
        job_manager.start()
        created = self.client.post(
            "/v1/templates/tpl_competitive_analysis/instantiate",
            json={"title": "Takeover Flow"},
            headers={"Authorization": "Bearer demo-admin"},
        )
        self.assertEqual(created.status_code, 200)
        workitem_id = created.json()["nodes"][0]["id"]
        job = job_manager.submit_run_agent(
            workitem_id=workitem_id,
            actor="u_test",
            payload={},
            runner=lambda _: {"ok": True},
        )

        resp = self.client.post(
            f"/v1/jobs/{job['id']}/human-takeover",
            json={"payload": {"note": "operator takeover"}},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["transition"]["to"], "in_progress")

    def test_escalate_job_to_task_creates_ops_task(self) -> None:
        job_manager.reset()
        job_manager.start()
        created = self.client.post(
            "/v1/templates/tpl_competitive_analysis/instantiate",
            json={"title": "Ops Task Flow"},
            headers={"Authorization": "Bearer demo-admin"},
        )
        self.assertEqual(created.status_code, 200)
        workitem = created.json()["nodes"][0]
        job = job_manager.submit_run_agent(
            workitem_id=workitem["id"],
            actor="u_test",
            payload={},
            runner=lambda _: {"ok": True},
        )

        resp = self.client.post(
            f"/v1/jobs/{job['id']}/escalate-task",
            json={"severity": "critical"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["task"]["workitem_id"], workitem["id"])
        listed = self.client.get(f"/v1/ops/tasks?workitem_id={workitem['id']}", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(len(listed.json()), 1)

    def test_runtime_reports_tasking_health_when_lark_ops_task_misconfigured(self) -> None:
        settings = replace(main_runtime_settings, ops_task_provider="lark_webhook", lark_task_webhook_url=None, lark_bot_webhook_url=None)
        with (
            patch("backend.app.main.runtime_settings", settings),
            patch("backend.app.healthcheck.RuntimeSettings", RuntimeSettings),
            patch("backend.app.healthcheck.tasking_service.health", return_value={"provider": "lark_webhook", "ok": False, "mode": "misconfigured", "error": "missing webhook"}),
            patch("backend.app.main.tasking_service.health", return_value={"provider": "lark_webhook", "ok": False, "mode": "misconfigured", "error": "missing webhook"}),
        ):
            runtime = self.client.get("/v1/runtime", headers=self.headers)
            health = self.client.get("/v1/runtime/health", headers=self.headers)

        self.assertEqual(runtime.status_code, 200)
        self.assertFalse(runtime.json()["tasking_health"]["ok"])
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["tasking"]["ok"])

    def test_lark_task_api_provider_creates_real_task_reference(self) -> None:
        settings = replace(
            main_runtime_settings,
            ops_task_provider="lark_task_api",
            lark_app_id="cli_xxx",
            lark_app_secret="secret_xxx",
            lark_tasklist_guid="tl_demo",
        )
        with (
            patch("backend.app.tasking.runtime_settings", settings),
            patch(
                "backend.app.tasking._http_json",
                side_effect=[
                    {"code": 0, "tenant_access_token": "tenant_token_demo", "expire": 7200},
                    {
                        "code": 0,
                        "data": {
                            "task": {
                                "guid": "task-guid-demo",
                                "url": "https://applink.feishu.cn/client/todo/detail?guid=task-guid-demo",
                            }
                        },
                    },
                ],
            ),
        ):
            result = tasking_service.create_task(
                {
                    "id": "task_local_1",
                    "title": "Investigate dead-letter job",
                    "summary": "Operator follow-up required",
                    "workitem_id": "wi_collect_competitors",
                    "severity": "critical",
                },
                settings,
            )

        self.assertEqual(result["provider"], "lark_task_api")
        self.assertEqual(result["delivery_status"], "delivered")
        self.assertEqual(result["guid"], "task-guid-demo")
        self.assertIn("applink.feishu.cn", result["task_url"])

    def test_list_ops_tasks_syncs_lark_task_status(self) -> None:
        settings = replace(
            main_runtime_settings,
            ops_task_provider="lark_task_api",
            lark_app_id="cli_xxx",
            lark_app_secret="secret_xxx",
            lark_tasklist_guid="tl_demo",
        )
        with (
            patch("backend.app.tasking.runtime_settings", settings),
            patch("backend.app.main.runtime_settings", settings),
            patch(
                "backend.app.tasking._http_json",
                side_effect=[
                    {"code": 0, "tenant_access_token": "tenant_token_demo", "expire": 7200},
                    {
                        "code": 0,
                        "data": {
                            "task": {
                                "guid": "task-guid-demo",
                                "url": "https://applink.feishu.cn/client/todo/detail?guid=task-guid-demo",
                            }
                        },
                    },
                    {
                        "code": 0,
                        "data": {
                            "task": {
                                "guid": "task-guid-demo",
                                "url": "https://applink.feishu.cn/client/todo/detail?guid=task-guid-demo",
                                "status": "done",
                                "completed_at": "1715652000000",
                            }
                        },
                    },
                ],
            ),
        ):
            created = store.create_ops_task(
                workitem_id="wi_collect_competitors",
                actor="u_yang",
                title="[Ops] Follow up",
                summary="Need operator check",
                source_kind="alert.resend",
                source_ref="alert_1",
            )
            created["last_synced_at"] = "1970-01-01T00:00:00+00:00"
            listed = self.client.get("/v1/ops/tasks?source_kind=alert.resend", headers=self.headers)

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["external_status"], "done")
        self.assertEqual(listed.json()[0]["status"], "completed")
        self.assertEqual(listed.json()[0]["completed_at"], "1715652000000")

    def test_list_ops_tasks_exposes_sync_error(self) -> None:
        settings = replace(
            main_runtime_settings,
            ops_task_provider="lark_task_api",
            lark_app_id="cli_xxx",
            lark_app_secret="secret_xxx",
            lark_tasklist_guid="tl_demo",
        )
        with (
            patch("backend.app.tasking.runtime_settings", settings),
            patch("backend.app.main.runtime_settings", settings),
            patch(
                "backend.app.tasking._http_json",
                side_effect=[
                    {"code": 0, "tenant_access_token": "tenant_token_demo", "expire": 7200},
                    {
                        "code": 0,
                        "data": {
                            "task": {
                                "guid": "task-guid-demo",
                                "url": "https://applink.feishu.cn/client/todo/detail?guid=task-guid-demo",
                            }
                        },
                    },
                    {"code": 99991663, "msg": "task not found"},
                ],
            ),
        ):
            created = store.create_ops_task(
                workitem_id="wi_collect_competitors",
                actor="u_yang",
                title="[Ops] Follow up",
                summary="Need operator check",
                source_kind="alert.silence",
                source_ref="cluster_1",
            )
            created["last_synced_at"] = "1970-01-01T00:00:00+00:00"
            listed = self.client.get("/v1/ops/tasks?source_kind=alert.silence", headers=self.headers)

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertIn("sync_error", listed.json()[0])
        self.assertEqual(listed.json()[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()

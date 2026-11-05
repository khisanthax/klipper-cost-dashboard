from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class BusinessOrderTests(unittest.TestCase):
    def setUp(self):
        from core import db, orders, projects
        from core.application_mode import set_application_mode
        from core.registration import register_printer
        import app

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        environment = patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"})
        environment.start()
        self.addCleanup(environment.stop)
        database = patch.object(db, "_db_path", return_value=str(Path(temp.name) / "kcd.db"))
        database.start()
        self.addCleanup(database.stop)
        register_printer("Fixture", "http://127.0.0.1:1")
        set_application_mode("business")
        self.project = projects.create_project("Order fixture")
        self.db, self.orders, self.projects = db, orders, projects
        self.client = app.app.test_client()

    def _add_job(self, uid, **costs):
        with self.db.connect_db() as conn:
            self.db.upsert_job(conn, {
                "printer": "Fixture", "filename": uid + ".gcode", "job_uid": uid,
                "status": "completed", **costs,
            })
        self.projects.assign_jobs([uid], self.project.id)

    def test_exact_costs_use_revenue_and_preserve_historical_rows(self):
        self._add_job("one", time_cost=1.005, material_cost=0.005, total_cost=1.02)
        self.projects.create_manual_job(self.project.id, "Setup", 0.1, cost_override=0.50)
        self.projects.create_plan_item(self.project.id, "future.gcode", 3600, est_cost_override=7.25)
        form = dict(project=self.project.id, status="active", priority="high", quote="10.00",
                    selling_price="9.99", revenue="8.00", expenses="0", estimated_cost="1.00")
        self.assertEqual(self.client.post("/orders", data=form).status_code, 303)
        order = self.orders.get_order(self.project.id)
        amounts = self.orders.financial_view(self.project, order)
        self.assertEqual(amounts["profit"], Decimal("6.48"))
        self.assertEqual(amounts["estimate_variance"], Decimal("0.52"))
        self.assertEqual(amounts["planned_cost"], Decimal("7.25"))
        self.assertEqual(amounts["actual"]["time_cost"] + amounts["actual"]["material_cost"]
                         + amounts["actual"]["adjustment"], amounts["actual"]["total_cost"])
        with self.db.connect_db() as conn:
            row = conn.execute("SELECT time_cost, material_cost, total_cost FROM jobs WHERE job_uid='one'").fetchone()
            self.assertEqual(tuple(row), (1.005, 0.005, 1.02))
        from core.sql_only import SqlOnlyFilesystemMonitor
        with SqlOnlyFilesystemMonitor():
            result = self.client.get("/orders", query_string={"project": self.project.id})
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"$6.48", result.data)
        self.assertIn(b"Saved historical cost", result.data)

    def test_unknown_costs_and_blank_expenses_do_not_make_profit(self):
        self._add_job("unpriced", time_cost=None, material_cost=None, total_cost=None)
        self.orders.save_order(self.project.id, dict(status="draft", priority="normal", revenue="8"))
        order = self.orders.get_order(self.project.id)
        amounts = self.orders.financial_view(self.project, order)
        self.assertIsNone(order["expenses"])
        self.assertIsNone(amounts["profit"])
        self.assertIsNone(amounts["actual_cost"])
        self.assertEqual(amounts["missing_totals"], 1)

    def test_revision_and_lifecycle_guards(self):
        self.orders.save_order(self.project.id, dict(status="draft", priority="normal"))
        first = self.orders.get_order(self.project.id)
        self.assertIsNone(first["completed_at"])
        with self.assertRaisesRegex(ValueError, "two decimal places"):
            self.orders.save_order(self.project.id, dict(status="active", priority="high", quote="3.333",
                                                        revision=first["updated_at"]))
        self.orders.save_order(self.project.id, dict(status="complete", priority="high", revision=first["updated_at"]))
        complete = self.orders.get_order(self.project.id)
        self.assertTrue(complete["completed_at"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.orders.save_order(self.project.id, dict(status="draft", priority="normal", revision=first["updated_at"]))
        with self.assertRaisesRegex(ValueError, "Only a draft"):
            self.orders.save_order(self.project.id, dict(action="delete", revision=complete["updated_at"]))
        with self.assertRaisesRegex(ValueError, "project has an order"):
            self.projects.delete_project(self.project.id)
        self.assertEqual(self.orders.get_order(self.project.id)["status"], "complete")

    def test_modes_gate_orders_links_and_writes_without_changing_order_records(self):
        from core.application_mode import set_application_mode

        self.orders.save_order(self.project.id, dict(status="active", priority="normal", customer="Private customer"))
        saved = self.orders.get_order(self.project.id)
        path = f"/orders?project={self.project.id}"
        for mode in ("advanced", "personal"):
            set_application_mode(mode)
            dashboard = self.client.get("/")
            projects = self.client.get("/projects")
            self.assertNotIn(b'href="/orders"', dashboard.data)
            self.assertIn(b'href="/projects"', dashboard.data)
            self.assertNotIn(b'href="/orders"', projects.data)
            blocked = self.client.get(path)
            self.assertEqual(blocked.status_code, 403)
            self.assertIn(b"Saved orders will be available", blocked.data)
            self.assertNotIn(b"Private customer", blocked.data)
            self.assertEqual(self.client.post("/orders", data=dict(project=self.project.id, action="delete")).status_code, 403)
            self.assertEqual(self.orders.get_order(self.project.id), saved)
        set_application_mode("business")
        self.assertIn(b'href="/orders"', self.client.get("/").data)
        self.assertIn(b"Private customer", self.client.get(path).data)
        self.assertEqual(self.orders.get_order(self.project.id), saved)

    def test_reopen_and_draft_deletion_preserve_assigned_jobs(self):
        self._add_job("kept-job", time_cost=1, material_cost=2, total_cost=2)
        with self.db.connect_db() as conn:
            before = tuple(conn.execute("SELECT * FROM jobs WHERE job_uid='kept-job'").fetchone())
        self.orders.save_order(self.project.id, dict(status="draft", priority="normal"))
        for status in ("active", "complete", "complete", "active", "draft"):
            old = self.orders.get_order(self.project.id)
            self.orders.save_order(self.project.id, dict(status=status, priority="normal", revision=old["updated_at"]))
            current = self.orders.get_order(self.project.id)
            if status == "complete":
                self.assertIsNotNone(current["completed_at"])
                if old["status"] == "complete":
                    self.assertEqual(current["completed_at"], old["completed_at"])
            else:
                self.assertIsNone(current["completed_at"])
        self.orders.save_order(self.project.id, dict(action="delete", revision=current["updated_at"]))
        self.assertIsNone(self.orders.get_order(self.project.id))
        self.assertIn(self.project.id, self.projects.load_projects())
        with self.db.connect_db() as conn:
            self.assertEqual(tuple(conn.execute("SELECT * FROM jobs WHERE job_uid='kept-job'").fetchone()), before)

    def test_zero_revenue_loss_and_unknown_revenue_are_distinct(self):
        self._add_job("expense", time_cost=1, material_cost=2, total_cost=2)
        for revenue, expected in (("0", Decimal("-2.00")), ("", None)):
            prior = self.orders.get_order(self.project.id)
            self.orders.save_order(self.project.id, dict(
                status="active", priority="normal", quote="100", selling_price="100", expenses="0",
                revenue=revenue, revision=prior["updated_at"] if prior else "",
            ))
            order = self.orders.get_order(self.project.id)
            financial = self.orders.financial_view(self.project, order)
            self.assertEqual(financial["actual"]["adjustment"], Decimal("-1.00"))
            self.assertEqual(financial["profit"], expected)
            self.assertIsNone(financial["margin"])

    def test_invalid_currency_values_do_not_replace_saved_amounts(self):
        self.orders.save_order(self.project.id, dict(status="draft", priority="normal", revenue="12.34"))
        before = self.orders.get_order(self.project.id)
        for value in ("-0.01", "NaN", "Infinity", "1.001", "1000000000.01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.orders.save_order(self.project.id, dict(
                    status="active", priority="normal", revision=before["updated_at"], revenue=value,
                ))
            self.assertEqual(self.orders.get_order(self.project.id), before)

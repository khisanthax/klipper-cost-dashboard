from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from core import db, inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        env = patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "sql"})
        env.start()
        self.addCleanup(env.stop)
        database_dir = patch.object(db, "DATA_DIR", scratch.name)
        database_dir.start()
        self.addCleanup(database_dir.stop)
        with closing(db.connect_db()) as conn, conn:
            db.apply_migrations(conn)
            self.printer_id = db.upsert_printer(conn, "Fixture printer")
            for uid, status in (("cancel", "canceled"), ("failed", "failed"), ("active", "printing")):
                db.upsert_job(conn, {
                    "job_uid": uid, "printer": "Fixture printer", "filename": uid + ".gcode",
                    "status": status, "filament_grams": 0.4, "filament_weight_kind": "estimated",
                    "total_cost": 2.50,
                })
        self.operation = uuid4()
        self.spool_args = dict(manufacturer="Example", material="PLA", color="Blue", net_grams="100",
                               remaining_grams="80", low_stock_grams="20", purchase_price=0)
        self.spool, _ = inventory.create_spool(self.operation, **self.spool_args)

    def test_create_assign_and_signed_adjustment_are_idempotent(self):
        inventory.assign_spool(self.spool, self.printer_id)
        adjustment = uuid4()
        self.assertTrue(inventory.adjust_stock(self.spool, adjustment, "-0.125", "Measured opening correction"))
        self.assertFalse(inventory.adjust_stock(self.spool, adjustment, "-0.125", "Measured opening correction"))
        with self.assertRaises(inventory.InventoryConflict):
            inventory.adjust_stock(self.spool, adjustment, "1", "Changed amount")
        self.assertEqual(inventory.create_spool(self.operation, **self.spool_args), (self.spool, False))
        spools, _, events, _ = inventory.inventory_view()
        self.assertEqual(spools[0]["remaining_mg"], 79_875)
        self.assertEqual(spools[0]["purchase_cents"], 0)
        self.assertEqual(spools[0]["printer_name"], "Fixture printer")
        self.assertEqual(len(events), 2)

    def test_concurrent_replay_deducts_a_canceled_job_once_and_preserves_history(self):
        barrier = Barrier(2)

        def record(_):
            barrier.wait(timeout=10)
            return inventory.record_consumption(self.spool, uuid4(), "cancel", "0.425", verified=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(record, range(2))), [False, True])
        with self.assertRaises(inventory.InventoryConflict):
            inventory.record_consumption(self.spool, uuid4(), "cancel", "0.426", verified=True)
        with closing(db.connect_db()) as conn, conn:
            job = conn.execute("SELECT total_cost, filament_grams FROM jobs WHERE job_uid='cancel'").fetchone()
            self.assertEqual(tuple(job), (2.50, 0.4))
            conn.execute("DELETE FROM jobs WHERE job_uid='cancel'")
        spools, _, events, _ = inventory.inventory_view()
        self.assertEqual(spools[0]["remaining_mg"], 79_575)
        consumed = [event for event in events if event["kind"] == "verified_consumption"]
        self.assertEqual([(event["job_uid"], event["job_filename"]) for event in consumed], [("cancel", "cancel.gcode")])

    def test_estimated_active_and_overdraw_inputs_cannot_change_inventory(self):
        for uid, grams, verified in (
            ("cancel", "0.4", False), ("active", "1", True), ("missing", "1", True),
            ("cancel", "81", True), ("cancel", "0.0001", True),
            ("cancel", "1e999999999", True), ("cancel", "NaN", True),
        ):
            with self.subTest(uid=uid, grams=grams, verified=verified), self.assertRaises(ValueError):
                inventory.record_consumption(self.spool, uuid4(), uid, grams, verified=verified)
        spools, _, events, _ = inventory.inventory_view()
        self.assertEqual(spools[0]["remaining_mg"], 80_000)
        self.assertEqual(len(events), 1)
        self.assertTrue(inventory.record_consumption(self.spool, uuid4(), "failed", "80", verified=True))
        self.assertEqual(inventory.inventory_view()[0][0]["remaining_mg"], 0)

    def test_compatibility_backends_do_not_read_inventory(self):
        with patch.dict(os.environ, {"KCD_STORAGE_BACKEND": "dual"}), patch.object(db, "connect_db") as connect:
            with self.assertRaisesRegex(ValueError, "SQL-only"):
                inventory.inventory_view()
            connect.assert_not_called()

    def test_linked_corrections_preserve_original_and_reject_stale_or_overdrawn_changes(self):
        original, correction = uuid4(), uuid4()
        inventory.record_consumption(self.spool, original, "cancel", "10", verified=True)
        kwargs = dict(expected_grams="10", reason="Scale reading corrected")
        self.assertTrue(inventory.correct_consumption(self.spool, correction, original, "12.345", **kwargs))
        self.assertFalse(inventory.correct_consumption(self.spool, correction, original, "12.345", **kwargs))
        for key, total, previous in ((correction, "12.346", "10"), (uuid4(), "1", "10"), (uuid4(), "90", "12.345")):
            with self.subTest(total=total, previous=previous), self.assertRaises(ValueError):
                inventory.correct_consumption(self.spool, key, original, total,
                                              expected_grams=previous, reason="Scale reading corrected")
        with closing(db.connect_db()) as conn, conn:
            original_row = conn.execute("SELECT delta_mg, job_uid FROM filament_stock_events WHERE event_uid=?", (original.hex,)).fetchone()
            self.assertEqual(tuple(original_row), (-10_000, "cancel"))
            self.assertEqual(conn.execute("SELECT total_cost FROM jobs WHERE job_uid='cancel'").fetchone()[0], 2.50)
            conn.execute("DELETE FROM jobs WHERE job_uid='cancel'")
        self.assertTrue(inventory.correct_consumption(self.spool, uuid4(), original, "0",
                                                      expected_grams="12.345", reason="Confirmed no use from this spool"))
        spool = inventory.inventory_view()[0][0]
        self.assertEqual(spool["remaining_mg"], 80_000)
        self.assertEqual(spool["consumptions"][0]["measured_mg"], 0)
        with closing(db.connect_db()) as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_estimates_are_separate_and_archival_preserves_every_balance_and_ledger(self):
        operation = uuid4()
        self.assertTrue(inventory.record_estimate(self.spool, operation, "active", "50.125", "Slicer estimate"))
        self.assertFalse(inventory.record_estimate(self.spool, operation, "active", "50.125", "Slicer estimate"))
        with self.assertRaises(inventory.InventoryConflict):
            inventory.record_estimate(self.spool, operation, "active", "50.126", "Slicer estimate")
        self.assertTrue(inventory.record_estimate(self.spool, uuid4(), "active", "48", "Updated slicer estimate"))
        archive = uuid4()
        self.assertTrue(inventory.set_spool_archived(self.spool, archive, archived=True, reason="Spool put in storage"))
        self.assertFalse(inventory.set_spool_archived(self.spool, archive, archived=True, reason="Spool put in storage"))
        for call in (
            lambda: inventory.record_consumption(self.spool, uuid4(), "failed", "1", verified=True),
            lambda: inventory.adjust_stock(self.spool, uuid4(), "1", "Changed scale"),
            lambda: inventory.assign_spool(self.spool, self.printer_id),
            lambda: inventory.record_estimate(self.spool, uuid4(), "active", "1", "Slicer estimate"),
        ):
            with self.assertRaisesRegex(ValueError, "Reactivate"):
                call()
        spool, _, events, _ = inventory.inventory_view()
        self.assertEqual(spool[0]["remaining_mg"], 80_000)
        self.assertEqual(spool[0]["estimates"][0]["estimated_mg"], 48_000)
        self.assertEqual(len(events), 1)
        self.assertTrue(inventory.set_spool_archived(self.spool, uuid4(), archived=False, reason="Returned to printer"))
        self.assertTrue(inventory.record_consumption(self.spool, uuid4(), "failed", "1", verified=True))

    def test_original_0010_database_upgrades_without_rewriting_ledger_or_jobs(self):
        with TemporaryDirectory() as directory, patch.object(db, "DATA_DIR", directory):
            files = db._list_migration_files()
            original_files = [path for path in files if path < "0011"]
            with closing(db.connect_db()) as conn, conn:
                with patch.object(db, "_list_migration_files", return_value=original_files):
                    db.apply_migrations(conn)
                db.upsert_printer(conn, "Original")
                db.upsert_job(conn, {"job_uid": "old-job", "printer": "Original", "filename": "old.gcode", "status": "canceled", "total_cost": 3.5})
                spool, original = uuid4().hex, uuid4().hex
                conn.execute("""INSERT INTO filament_spools
                    (spool_uid,manufacturer,material,color,net_mg,remaining_mg,low_stock_mg,created_at)
                    VALUES (?,'Maker','PLA','Blue',100000,90000,1000,'2026-11-01T00:00:00Z')""", (spool,))
                conn.execute("""INSERT INTO filament_stock_events
                    (event_uid,spool_uid,kind,delta_mg,job_uid,job_filename,reason,recorded_at)
                    VALUES (?,?,'verified_consumption',-10000,'old-job','old.gcode','Original measurement','2026-11-01T01:00:00Z')""", (original, spool))
                before = tuple(conn.execute("SELECT * FROM filament_stock_events").fetchone())
                db.apply_migrations(conn)
                self.assertEqual(tuple(conn.execute("SELECT * FROM filament_stock_events").fetchone()), before)
            inventory.correct_consumption(spool, uuid4(), original, "12", expected_grams="10", reason="New scale measurement")
            with closing(db.connect_db()) as conn:
                self.assertEqual(conn.execute("SELECT remaining_mg FROM filament_spools").fetchone()[0], 88_000)
                self.assertEqual(conn.execute("SELECT total_cost FROM jobs").fetchone()[0], 3.5)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_concurrent_corrections_require_the_current_measurement(self):
        original = uuid4()
        inventory.record_consumption(self.spool, original, "failed", "10", verified=True)
        barrier = Barrier(2)

        def correct(total):
            barrier.wait(timeout=10)
            try:
                return inventory.correct_consumption(self.spool, uuid4(), original, total,
                                                     expected_grams="10", reason="Concurrent scale reading")
            except inventory.InventoryConflict:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(correct, ("11", "12")))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count("stale"), 1)
        spool = inventory.inventory_view()[0][0]
        measured = spool["consumptions"][0]["measured_mg"]
        self.assertIn(measured, (11_000, 12_000))
        self.assertEqual(spool["remaining_mg"], 80_000 - measured)

    def test_multiple_spools_and_failed_ledger_insert_keep_exact_balances(self):
        second, _ = inventory.create_spool(uuid4(), **self.spool_args)
        inventory.record_consumption(self.spool, uuid4(), "cancel", "1.125", verified=True)
        inventory.record_consumption(second, uuid4(), "cancel", "2.250", verified=True)
        with closing(db.connect_db()) as conn, conn:
            conn.execute("""CREATE TRIGGER injected_stock_failure BEFORE INSERT ON filament_stock_events
                         WHEN NEW.reason='Injected failure' BEGIN SELECT RAISE(ABORT, 'test write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            inventory.adjust_stock(self.spool, uuid4(), "-3", "Injected failure")
        spools = {row["spool_uid"]: row for row in inventory.inventory_view()[0]}
        self.assertEqual(spools[self.spool]["remaining_mg"], 78_875)
        self.assertEqual(spools[second]["remaining_mg"], 77_750)
        with closing(db.connect_db()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filament_stock_events WHERE reason='Injected failure'").fetchone()[0], 0)

    def test_personal_mode_hides_only_inventory_navigation_and_keeps_default_visibility(self):
        from flask import Flask, render_template
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        app.jinja_env.globals["url_for"] = lambda endpoint, **_kwargs: "/" + endpoint
        with app.test_request_context("/"):
            normal = render_template("base.html", inventory_available=True)
            personal = render_template("base.html", inventory_available=True, show_advanced=False)
            advanced = render_template("base.html", inventory_available=True, show_advanced=True)
            compatibility = render_template("base.html", inventory_available=False, show_advanced=True)
            demo = render_template("base.html", inventory_available=True, show_advanced=True,
                                   kcd_demo_mode=True, demo_endpoint_available=lambda _endpoint: False)
        self.assertIn('href="/inventory_page"', normal)
        self.assertIn('href="/inventory_page"', advanced)
        self.assertNotIn('href="/inventory_page"', personal)
        self.assertNotIn('href="/inventory_page"', compatibility)
        self.assertNotIn('href="/inventory_page"', demo)
        self.assertIn('href="/projects_page"', demo)
        self.assertIn('href="/projects_page"', personal)

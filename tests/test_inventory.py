from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
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

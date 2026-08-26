from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from flask import Flask, g, jsonify, request

from core import db as db_module
from core.demo.flask_integration import install_workspace_runtime
from core.demo.manager import WorkspaceBusy
from core.workspace_context import WorkspaceContext


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.value

    def advance(self, **kwargs):
        with self._lock:
            self.value += timedelta(**kwargs)


def build_seed(data_root: Path) -> Path:
    seed = data_root / "seeds" / "basic-v1"
    seed.mkdir(parents=True)
    context = WorkspaceContext(
        "normal", None, seed, seed / "kcd.db", seed / "cache", seed / "exports",
        seed / "temp", seed / "uploads", None, "sql",
    )
    with closing(db_module.connect_db(context)) as conn:
        db_module.apply_migrations(conn)
        now = "2026-08-26T12:00:00+00:00"
        conn.execute(
            "INSERT INTO printers(name, created_at, updated_at) VALUES(?,?,?)",
            ("Seed Printer", now, now),
        )
        conn.commit()
    return seed


class DemoConcurrencyCharacterizationTests(unittest.TestCase):
    """Characterize the foundation lock before the Phase 3.5 reader/writer fix."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo-data"
        self.root.mkdir()
        build_seed(self.root)
        self.env = mock.patch.dict(os.environ, {
            "KCD_RUNTIME_MODE": "demo",
            "KCD_DEMO_DATA_ROOT": str(self.root),
            "KCD_STORAGE_BACKEND": "sql",
            "KCD_REPORTS_BACKEND": "sql",
            "KCD_DEMO_COOKIE_SECURE": "0",
        }, clear=False)
        self.env.start()
        self.app = Flask(__name__)
        self.manager = install_workspace_runtime(self.app)
        self.clock = MutableClock()
        self.manager.clock = self.clock
        self.manager.registry.clock = self.clock
        self.hold_started = threading.Event()
        self.hold_release = threading.Event()
        self.reset_started = threading.Event()
        self.reset_release = threading.Event()

        @self.app.get("/probe/read")
        def probe_read():
            if request.args.get("hold") == "1":
                self.hold_started.set()
                if not self.hold_release.wait(timeout=5):
                    return jsonify({"status": "timeout"}), 504
            with closing(db_module.connect_db()) as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM printers ORDER BY name")]
            return jsonify({"names": names, "workspace": g.workspace_context.workspace_id})

        @self.app.post("/probe/mutate")
        def probe_mutate():
            if request.args.get("hold") == "1":
                self.hold_started.set()
                if not self.hold_release.wait(timeout=5):
                    return jsonify({"status": "timeout"}), 504
            with closing(db_module.connect_db()) as conn:
                conn.execute(
                    "INSERT INTO printers(name,created_at,updated_at) VALUES(?,?,?)",
                    (request.get_json()["name"], "x", "x"),
                )
                conn.commit()
            return jsonify({"status": "mutated"})

        @self.app.get("/probe/error")
        def probe_error():
            raise RuntimeError("deterministic request failure")

        self.app.testing = False

    def tearDown(self):
        self.hold_release.set()
        self.reset_release.set()
        self.env.stop()
        self.tmp.cleanup()

    def _new_client(self):
        client = self.app.test_client()
        response = client.post("/demo/foundation/session")
        self.assertEqual(response.status_code, 201)
        return client

    def _copy_session(self, source):
        target = self.app.test_client()
        target.set_cookie("kcd_demo_session", source.get_cookie("kcd_demo_session").value)
        return target

    def _held_request(self, client, path="/probe/read?hold=1", method="get", **kwargs):
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(getattr(client, method), path, **kwargs)
        self.assertTrue(self.hold_started.wait(timeout=2), "held request did not start")
        return pool, future

    def _finish_held(self, pool, future):
        self.hold_release.set()
        try:
            response = future.result(timeout=3)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertEqual(response.status_code, 200)
        return response

    def test_same_workspace_overlapping_reads_currently_conflict(self):
        first = self._new_client()
        second = self._copy_session(first)
        pool, active = self._held_request(first)
        try:
            self.assertEqual(second.get("/probe/read").status_code, 409)
        finally:
            self._finish_held(pool, active)

    def test_same_workspace_read_and_mutation_currently_conflict(self):
        reader = self._new_client()
        writer = self._copy_session(reader)
        pool, active = self._held_request(reader)
        try:
            self.assertEqual(
                writer.post("/probe/mutate", json={"name": "Writer"}).status_code,
                409,
            )
        finally:
            self._finish_held(pool, active)

    def test_same_workspace_mutations_currently_conflict(self):
        first = self._new_client()
        second = self._copy_session(first)
        pool, active = self._held_request(
            first, "/probe/mutate?hold=1", method="post", json={"name": "First"}
        )
        try:
            self.assertEqual(
                second.post("/probe/mutate", json={"name": "Second"}).status_code,
                409,
            )
        finally:
            self._finish_held(pool, active)

    def test_different_workspaces_do_not_conflict(self):
        first = self._new_client()
        second = self._new_client()
        pool, active = self._held_request(first)
        try:
            self.assertEqual(second.get("/probe/read").status_code, 200)
            self.assertEqual(
                second.post("/probe/mutate", json={"name": "Second workspace"}).status_code,
                200,
            )
        finally:
            self._finish_held(pool, active)

    def test_reset_conflicts_with_active_request(self):
        active_client = self._new_client()
        resetter = self._copy_session(active_client)
        pool, active = self._held_request(active_client)
        try:
            self.assertEqual(resetter.post("/demo/foundation/reset").status_code, 409)
        finally:
            self._finish_held(pool, active)
        self.assertEqual(resetter.post("/demo/foundation/reset").status_code, 200)

    def test_cleanup_skips_active_request_then_deletes(self):
        active_client = self._new_client()
        token = active_client.get_cookie("kcd_demo_session").value
        record = self.manager.resolve_workspace(token).record
        pool, active = self._held_request(active_client)
        try:
            self.clock.advance(hours=25)
            self.assertEqual(
                self.manager.cleanup_expired_workspaces(), [record.workspace_id]
            )
            self.assertIsNotNone(self.manager.registry.get(record.workspace_id))
        finally:
            self._finish_held(pool, active)
        self.manager.cleanup_expired_workspaces()
        self.assertIsNone(self.manager.registry.get(record.workspace_id))

    def test_expiration_can_mark_active_request_but_delete_conflicts(self):
        active_client = self._new_client()
        token = active_client.get_cookie("kcd_demo_session").value
        record = self.manager.resolve_workspace(token).record
        pool, active = self._held_request(active_client)
        try:
            expired = self.manager.expire_workspace(record)
            self.assertEqual(expired.state, "expired")
            with self.assertRaises(WorkspaceBusy):
                self.manager.delete_workspace(expired)
        finally:
            self._finish_held(pool, active)
        self.manager.delete_workspace(self.manager.registry.get(record.workspace_id))

    def test_two_resets_are_controlled(self):
        first = self._new_client()
        second = self._copy_session(first)
        original_clone = self.manager._clone_seed

        def held_clone(target):
            if target.name.startswith(".resetting-"):
                self.reset_started.set()
                if not self.reset_release.wait(timeout=5):
                    raise TimeoutError("reset test timed out")
            return original_clone(target)

        with mock.patch.object(self.manager, "_clone_seed", side_effect=held_clone):
            with ThreadPoolExecutor(max_workers=1) as pool:
                active = pool.submit(first.post, "/demo/foundation/reset")
                self.assertTrue(self.reset_started.wait(timeout=2))
                self.assertEqual(second.post("/demo/foundation/reset").status_code, 410)
                self.reset_release.set()
                self.assertEqual(active.result(timeout=3).status_code, 200)

    def test_requests_during_resetting_or_deleting_are_gone(self):
        resetting = self._new_client()
        resetting_token = resetting.get_cookie("kcd_demo_session").value
        resetting_record = self.manager.resolve_workspace(resetting_token).record
        self.manager.registry.transition(resetting_record.workspace_id, "active", "resetting")
        self.assertEqual(resetting.get("/probe/read").status_code, 410)

        deleting = self._new_client()
        deleting_token = deleting.get_cookie("kcd_demo_session").value
        deleting_record = self.manager.resolve_workspace(deleting_token).record
        self.manager.registry.transition(deleting_record.workspace_id, "active", "deleting")
        self.assertEqual(deleting.get("/probe/read").status_code, 410)

    def test_teardown_releases_lock_after_exception(self):
        client = self._new_client()
        peer = self._copy_session(client)
        self.assertEqual(client.get("/probe/error").status_code, 500)
        self.assertEqual(peer.get("/probe/read").status_code, 200)


if __name__ == "__main__":
    unittest.main()

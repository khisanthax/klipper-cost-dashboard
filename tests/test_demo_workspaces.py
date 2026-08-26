from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from flask import Flask, g, jsonify, request

from core import db as db_module
from core.demo.config import DemoConfiguration, load_demo_configuration
from core.demo.flask_integration import install_workspace_runtime
from core.demo.manager import (
    DemoWorkspaceManager, SeedValidationError, UnsafeWorkspacePath,
    WorkspaceExpired, WorkspaceNotFound, WorkspaceSession,
)
from core.demo.registry import InvalidStateTransition
from core.runtime import RuntimeConfigurationError, runtime_mode
from core.workspace_context import WorkspaceContext, WorkspaceContextError


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, **kwargs):
        with self.lock:
            self.value += timedelta(**kwargs)


def _context(root: Path) -> WorkspaceContext:
    return WorkspaceContext(
        "normal", None, root, root / "kcd.db", root / "cache", root / "exports",
        root / "temp", root / "uploads", None, "sql",
    )


def build_seed(data_root: Path, seed_name: str = "basic-v1") -> Path:
    seed = data_root / "seeds" / seed_name
    seed.mkdir(parents=True)
    with closing(db_module.connect_db(_context(seed))) as conn:
        db_module.apply_migrations(conn)
        now = "2026-08-23T12:00:00+00:00"
        conn.execute(
            "INSERT INTO printers(name, created_at, updated_at) VALUES(?,?,?)",
            ("Demo Printer", now, now),
        )
        conn.execute(
            """INSERT INTO filament_profiles(profile_uid,name,material,filament_mode,
               filament_rate,grams_per_meter,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("seed-filament", "Basic PLA", "PLA", "per_kg", 20.0, 3.0, now, now),
        )
        conn.execute(
            "INSERT INTO hourly_rate_profiles(profile_uid,name,rate_per_hour,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("seed-rate", "Basic Rate", 2.0, now, now),
        )
        conn.execute(
            "INSERT INTO projects(project_uid,name,created_at,updated_at) VALUES(?,?,?,?)",
            ("seed-project", "Basic Project", now, now),
        )
        db_module.upsert_job(conn, {
            "job_uid": "seed-job", "printer": "Demo Printer", "filename": "cube.gcode",
            "status": "completed", "timestamp": 1787500800, "duration_seconds": 600,
            "filament_mm": 1000, "total_cost": 1.25,
        })
        conn.commit()
    return seed


class DemoWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo-data"
        self.root.mkdir()
        self.seed = build_seed(self.root)
        self.clock = MutableClock()
        self.config = DemoConfiguration(
            self.root, "basic-v1", 24, 72, 300, cookie_secure=False
        )
        self.manager = DemoWorkspaceManager(self.config, clock=self.clock)
        self.manager.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def scalar(context, sql, params=()):
        with closing(db_module.connect_db(context)) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_registry_schema_state_touch_and_expiration(self):
        session = self.manager.create_workspace()
        record = self.manager.registry.get(session.record.workspace_id)
        self.assertEqual(record.state, "active")
        self.assertIsNotNone(record.created_at.tzinfo)
        with self.manager.registry._connect() as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(demo_workspaces)")}
            lease_indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(demo_workspace_leases)")
            }
        self.assertIn("idx_demo_token", indexes)
        self.assertIn("idx_demo_expiration", indexes)
        self.assertIn("idx_demo_lease_workspace", lease_indexes)
        self.assertIn("idx_demo_lease_expiration", lease_indexes)
        with self.assertRaises(sqlite3.IntegrityError):
            duplicate = record.__class__(
                "different", record.session_token_hash, record.created_at, record.updated_at,
                record.last_seen_at, record.idle_expires_at, record.absolute_expires_at,
                "active", record.seed_version, "different", 0, None, None,
            )
            self.manager.registry.insert(duplicate)
        self.assertFalse(self.manager.touch_workspace(record))
        self.clock.advance(seconds=301)
        self.assertTrue(self.manager.touch_workspace(record))
        with self.assertRaises(InvalidStateTransition):
            self.manager.registry.transition(record.workspace_id, "active", "creating")

    def test_create_isolate_modify_reset_and_seed_immutable(self):
        seed_hash = hashlib.sha256((self.seed / "kcd.db").read_bytes()).hexdigest()
        a = self.manager.create_workspace()
        b = self.manager.create_workspace()
        self.assertNotEqual(a.token, b.token)
        self.assertNotEqual(a.context.database_path, b.context.database_path)
        for field in (
            "workspace_root", "reports_cache_path", "exports_path",
            "temporary_path", "uploads_path",
        ):
            self.assertNotEqual(getattr(a.context, field), getattr(b.context, field))
        self.assertEqual(self.scalar(a.context, "SELECT COUNT(*) FROM printers"), 1)
        self.assertEqual(self.scalar(b.context, "SELECT COUNT(*) FROM printers"), 1)
        with closing(db_module.connect_db(a.context)) as conn:
            conn.execute(
                "INSERT INTO printers(name,created_at,updated_at) VALUES('A only','x','x')"
            )
            conn.execute(
                "INSERT INTO projects(project_uid,name,created_at,updated_at) VALUES('a','A project','x','x')"
            )
            conn.commit()
        with closing(db_module.connect_db(b.context)) as conn:
            conn.execute(
                "UPDATE filament_profiles SET name='B filament' WHERE profile_uid='seed-filament'"
            )
            conn.execute(
                "UPDATE hourly_rate_profiles SET name='B rate' WHERE profile_uid='seed-rate'"
            )
            conn.commit()
        with closing(db_module.connect_db(a.context)) as conn:
            conn.execute(
                "INSERT INTO report_cache VALUES('summary','all',1,'same',1,'{\"owner\":\"A\"}')"
            )
            conn.commit()
        with closing(db_module.connect_db(b.context)) as conn:
            conn.execute(
                "INSERT INTO report_cache VALUES('summary','all',1,'same',1,'{\"owner\":\"B\"}')"
            )
            conn.commit()
        self.assertEqual(self.scalar(a.context, "SELECT COUNT(*) FROM printers"), 2)
        self.assertEqual(self.scalar(b.context, "SELECT COUNT(*) FROM printers"), 1)
        self.assertEqual(self.scalar(a.context, "SELECT name FROM filament_profiles"), "Basic PLA")
        self.assertEqual(self.scalar(b.context, "SELECT name FROM filament_profiles"), "B filament")
        self.assertEqual(
            self.scalar(a.context, "SELECT payload_json FROM report_cache"), '{"owner":"A"}'
        )
        self.assertEqual(
            self.scalar(b.context, "SELECT payload_json FROM report_cache"), '{"owner":"B"}'
        )
        reset = self.manager.reset_workspace(a)
        self.assertEqual(reset.token, a.token)
        self.assertEqual(reset.record.workspace_id, a.record.workspace_id)
        self.assertEqual(reset.record.reset_count, 1)
        self.assertEqual(self.scalar(reset.context, "SELECT COUNT(*) FROM printers"), 1)
        self.assertEqual(self.scalar(reset.context, "SELECT COUNT(*) FROM report_cache"), 0)
        self.assertEqual(self.scalar(b.context, "SELECT COUNT(*) FROM report_cache"), 1)
        self.assertEqual(self.scalar(b.context, "SELECT name FROM filament_profiles"), "B filament")
        self.assertEqual(hashlib.sha256((self.seed / "kcd.db").read_bytes()).hexdigest(), seed_hash)

    def test_expiration_touch_absolute_limit_and_cleanup(self):
        session = self.manager.create_workspace()
        self.clock.advance(hours=23, minutes=59)
        current = self.manager.resolve_workspace(session.token)
        self.manager.touch_workspace(current.record)
        self.clock.advance(hours=24, minutes=1)
        with self.assertRaises(WorkspaceExpired):
            self.manager.resolve_workspace(session.token)
        self.manager.delete_workspace(self.manager.registry.get(session.record.workspace_id))
        with self.assertRaises(WorkspaceNotFound):
            self.manager.resolve_workspace(session.token)

        absolute = self.manager.create_workspace()
        for _ in range(2):
            self.clock.advance(hours=23)
            current = self.manager.resolve_workspace(absolute.token)
            self.manager.touch_workspace(current.record)
        self.clock.advance(hours=26)
        with self.assertRaises(WorkspaceExpired):
            self.manager.resolve_workspace(absolute.token)

    def test_cleanup_one_workspace_preserves_the_other(self):
        seed_hash = hashlib.sha256((self.seed / "kcd.db").read_bytes()).hexdigest()
        a = self.manager.create_workspace()
        b = self.manager.create_workspace()
        with closing(db_module.connect_db(b.context)) as conn:
            conn.execute(
                "INSERT INTO printers(name,created_at,updated_at) VALUES('B survives','x','x')"
            )
            conn.execute(
                "INSERT INTO report_cache VALUES('b','all',1,'b',1,'{\"owner\":\"B\"}')"
            )
            conn.commit()
        with self.manager.registry._connect() as conn:
            conn.execute(
                "UPDATE demo_workspaces SET idle_expires_at=? WHERE workspace_id=?",
                ((self.clock() - timedelta(seconds=1)).isoformat(), a.record.workspace_id),
            )
        self.assertEqual(self.manager.cleanup_expired_workspaces(), [a.record.workspace_id])
        self.assertIsNone(self.manager.registry.get(a.record.workspace_id))
        self.assertFalse(a.context.workspace_root.exists())
        self.assertEqual(self.scalar(b.context, "SELECT COUNT(*) FROM printers"), 2)
        self.assertEqual(
            self.scalar(b.context, "SELECT payload_json FROM report_cache"),
            '{"owner":"B"}',
        )
        self.assertEqual(hashlib.sha256((self.seed / "kcd.db").read_bytes()).hexdigest(), seed_hash)

    def test_invalid_seed_and_path_safety(self):
        (self.seed / "kcd.db").unlink()
        with self.assertRaises(SeedValidationError):
            self.manager.create_workspace()
        build_seed(self.root, "replacement")
        config = DemoConfiguration(self.root, "replacement", 24, 72, 300, cookie_secure=False)
        manager = DemoWorkspaceManager(config, clock=self.clock)
        manager.initialize()
        session = manager.create_workspace()
        malicious = session.record.__class__(
            session.record.workspace_id, session.record.session_token_hash,
            session.record.created_at, session.record.updated_at, session.record.last_seen_at,
            session.record.idle_expires_at, session.record.absolute_expires_at, session.record.state,
            session.record.seed_version, "../outside", session.record.reset_count, None, None,
        )
        with self.assertRaises(UnsafeWorkspacePath):
            manager._context(malicious)
        absolute = malicious.__class__(
            malicious.workspace_id, malicious.session_token_hash, malicious.created_at,
            malicious.updated_at, malicious.last_seen_at, malicious.idle_expires_at,
            malicious.absolute_expires_at, malicious.state, malicious.seed_version,
            str(Path(self.tmp.name).resolve()), malicious.reset_count, None, None,
        )
        with self.assertRaises(UnsafeWorkspacePath):
            manager._context(absolute)
    def test_workspace_symlink_escape_is_rejected(self):
        session = self.manager.create_workspace()
        outside = Path(self.tmp.name) / "outside-workspace"
        outside.mkdir()
        link = session.context.workspace_root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(UnsafeWorkspacePath):
            self.manager.validate_workspace_path(session.context.workspace_root)

    def test_seed_symlink_is_rejected(self):
        outside_db = Path(self.tmp.name) / "outside-seed.db"
        (self.seed / "kcd.db").replace(outside_db)
        try:
            (self.seed / "kcd.db").symlink_to(outside_db)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(UnsafeWorkspacePath):
            self.manager.validate_seed()

    def test_cleanup_containment_preserves_outside_directory(self):
        session = self.manager.create_workspace()
        outside = Path(self.tmp.name) / "outside-cleanup"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        now = self.clock()
        malicious = replace(
            session.record,
            workspace_id="malicious-cleanup",
            session_token_hash="f" * 64,
            relative_storage_path="../outside-cleanup",
            idle_expires_at=now - timedelta(seconds=1),
        )
        self.manager.registry.insert(malicious)
        self.manager.cleanup_expired_workspaces()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.manager.registry.get(malicious.workspace_id).state, "error")

    def test_reset_uses_registry_path_and_restores_after_replace_failure(self):
        session = self.manager.create_workspace()
        with closing(db_module.connect_db(session.context)) as conn:
            conn.execute(
                "INSERT INTO printers(name,created_at,updated_at) VALUES('keep','x','x')"
            )
            conn.commit()
        outside = Path(self.tmp.name) / "outside-reset"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        forged = WorkspaceSession(
            session.token,
            replace(session.context, workspace_root=outside, database_path=outside / "kcd.db"),
            session.record,
        )
        real_replace = os.replace

        def fail_new_workspace(source, target):
            source_path = Path(source)
            target_path = Path(target)
            if source_path.name.startswith(".resetting-") and target_path == session.context.workspace_root:
                raise OSError("injected atomic replacement failure")
            return real_replace(source, target)

        with mock.patch("core.demo.manager.os.replace", side_effect=fail_new_workspace):
            with self.assertRaises(OSError):
                self.manager.reset_workspace(forged)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(
            self.scalar(session.context, "SELECT COUNT(*) FROM printers WHERE name='keep'"), 1
        )
        self.assertEqual(
            self.manager.registry.get(session.record.workspace_id).state, "error"
        )

    def test_simultaneous_creations_are_distinct(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            sessions = list(pool.map(lambda _value: self.manager.create_workspace(), range(2)))
        self.assertEqual(len({s.record.workspace_id for s in sessions}), 2)
        self.assertEqual(len({s.record.session_token_hash for s in sessions}), 2)

    def test_invalid_sqlite_seed_and_cleanup_are_fail_closed(self):
        bad_root = Path(self.tmp.name) / "bad-demo"
        bad_seed = bad_root / "seeds" / "bad"
        bad_seed.mkdir(parents=True)
        (bad_seed / "kcd.db").write_text("not sqlite", encoding="utf-8")
        bad = DemoWorkspaceManager(
            DemoConfiguration(bad_root, "bad", 24, 72, 300, cookie_secure=False),
            clock=self.clock,
        )
        with self.assertRaises(SeedValidationError):
            bad.initialize()

        session = self.manager.create_workspace()
        lease = self.manager.acquire_request_lease(session.record, heartbeat=False)
        with self.manager.registry._connect() as conn:
            conn.execute(
                "UPDATE demo_workspaces SET idle_expires_at=? WHERE workspace_id=?",
                ((self.clock() - timedelta(seconds=1)).isoformat(), session.record.workspace_id),
            )
        self.assertEqual(self.manager.cleanup_expired_workspaces(dry_run=True), [session.record.workspace_id])
        self.assertTrue(session.context.workspace_root.exists())
        try:
            self.manager.cleanup_expired_workspaces()
            self.assertIsNotNone(self.manager.registry.get(session.record.workspace_id))
        finally:
            lease.release()
        self.manager.cleanup_expired_workspaces()
        self.assertFalse(session.context.workspace_root.exists())
        self.assertIsNone(self.manager.registry.get(session.record.workspace_id))


class DemoFlaskIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo-data"
        self.root.mkdir()
        build_seed(self.root)
        self.env = mock.patch.dict(os.environ, {
            "KCD_RUNTIME_MODE": "demo", "KCD_DEMO_DATA_ROOT": str(self.root),
            "KCD_STORAGE_BACKEND": "sql", "KCD_REPORTS_BACKEND": "sql",
            "KCD_DEMO_COOKIE_SECURE": "0",
        }, clear=False)
        self.env.start()
        self.app = Flask(__name__)
        self.manager = install_workspace_runtime(self.app)
        self.leaked_connections = []
        self.hold_started = threading.Event()
        self.hold_release = threading.Event()

        @self.app.route("/test/state", methods=["GET", "POST"])
        def state():
            context = g.workspace_context
            with closing(db_module.connect_db()) as conn:
                if request.method == "POST":
                    conn.execute(
                        "INSERT INTO printers(name,created_at,updated_at) VALUES(?,?,?)",
                        (request.json["name"], "x", "x"),
                    )
                    conn.commit()
                names = [row[0] for row in conn.execute("SELECT name FROM printers ORDER BY name")]
                cache_key = request.args.get("cache")
                if cache_key:
                    conn.execute(
                        "INSERT OR REPLACE INTO report_cache VALUES(?,?,?,?,?,?)",
                        (cache_key, "all", 1, "fp", 1, request.json.get("payload", "{}") if request.is_json else "{}"),
                    )
                    conn.commit()
            return jsonify({"names": names, "db": str(context.database_path) if self.app.testing else "scoped"})

        @self.app.get("/test/leak")
        def leak():
            connection = db_module.connect_db()
            connection.execute("SELECT 1").fetchone()
            self.leaked_connections.append(connection)
            return jsonify({"status": "opened"})

        @self.app.get("/test/hold")
        def hold():
            self.hold_started.set()
            self.hold_release.wait(timeout=5)
            return jsonify({"status": "released"})

        self.app.testing = True

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_two_clients_request_scoped_and_fail_closed(self):
        a = self.app.test_client()
        b = self.app.test_client()
        self.assertEqual(a.get("/test/state").status_code, 401)
        self.assertEqual(a.post("/demo/foundation/session").status_code, 201)
        self.assertEqual(b.post("/demo/foundation/session").status_code, 201)
        a_initial = a.get("/test/state").get_json()
        b_initial = b.get("/test/state").get_json()
        self.assertNotEqual(a_initial["db"], b_initial["db"])
        a.post("/test/state", json={"name": "A only"})
        b.post("/test/state", json={"name": "B only"})
        self.assertIn("A only", a.get("/test/state").get_json()["names"])
        self.assertNotIn("A only", b.get("/test/state").get_json()["names"])
        self.assertIn("B only", b.get("/test/state").get_json()["names"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.get("/test/state"), (a, b)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        guessed = self.app.test_client()
        guessed.set_cookie("kcd_demo_session", self.manager.registry.all()[0].workspace_id)
        self.assertEqual(guessed.get("/test/state").status_code, 401)
        self.assertEqual(a.post("/demo/foundation/reset").status_code, 200)
        self.assertNotIn("A only", a.get("/test/state").get_json()["names"])
        self.assertIn("B only", b.get("/test/state").get_json()["names"])

    def test_session_cookie_and_token_redaction_contract(self):
        client = self.app.test_client()
        response = client.post("/demo/foundation/session")
        self.assertEqual(response.get_json(), {"status": "created"})
        header = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertIn("Path=/", header)
        self.assertIn("Max-Age=259200", header)
        self.assertNotIn("Secure", header)
        token = client.get_cookie("kcd_demo_session").value
        record = self.manager.registry.all()[0]
        self.assertNotEqual(record.session_token_hash, token)
        self.assertEqual(record.session_token_hash, self.manager.hash_token(token))
        self.assertNotIn(token, response.get_data(as_text=True))
        self.assertNotIn(record.workspace_id, response.get_data(as_text=True))

        lease = self.manager.acquire_request_lease(record, heartbeat=False)
        with mock.patch.object(
            self.manager.registry,
            "release_lease",
            side_effect=sqlite3.OperationalError("injected release failure"),
        ):
            with self.assertLogs("core.demo.manager", level="WARNING") as captured:
                lease.release()
        log_output = "\n".join(captured.output)
        self.assertNotIn(token, log_output)
        self.assertNotIn(record.workspace_id, log_output)

    def test_secure_cookie_can_be_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "secure-demo"
            root.mkdir()
            build_seed(root)
            with mock.patch.dict(os.environ, {
                "KCD_RUNTIME_MODE": "demo",
                "KCD_DEMO_DATA_ROOT": str(root),
                "KCD_STORAGE_BACKEND": "sql",
                "KCD_REPORTS_BACKEND": "sql",
                "KCD_DEMO_COOKIE_SECURE": "1",
            }, clear=False):
                secure_app = Flask("secure-cookie-test")
                install_workspace_runtime(secure_app)
                response = secure_app.test_client().post("/demo/foundation/session")
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_reset_refuses_to_race_an_active_request(self):
        creator = self.app.test_client()
        creator.post("/demo/foundation/session")
        token = creator.get_cookie("kcd_demo_session").value
        holder = self.app.test_client()
        resetter = self.app.test_client()
        holder.set_cookie("kcd_demo_session", token)
        resetter.set_cookie("kcd_demo_session", token)
        with ThreadPoolExecutor(max_workers=1) as pool:
            active = pool.submit(holder.get, "/test/hold")
            self.assertTrue(self.hold_started.wait(timeout=2))
            self.assertEqual(resetter.post("/demo/foundation/reset").status_code, 409)
            self.hold_release.set()
            self.assertEqual(active.result(timeout=3).status_code, 200)
        self.assertEqual(resetter.post("/demo/foundation/reset").status_code, 200)

    def test_connections_close_and_context_does_not_leak(self):
        client = self.app.test_client()
        client.post("/demo/foundation/session")
        first = client.get("/test/state").get_json()["db"]
        second = client.get("/test/state").get_json()["db"]
        self.assertEqual(first, second)
        self.assertEqual(client.get("/test/leak").status_code, 200)
        with self.assertRaises(sqlite3.ProgrammingError):
            self.leaked_connections[-1].execute("SELECT 1")
        with self.assertRaises(WorkspaceContextError):
            db_module.connect_db()


class RuntimeConfigurationTests(unittest.TestCase):
    def test_normal_default_and_demo_fail_closed_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime_mode(), "normal")
        with mock.patch.dict(os.environ, {"KCD_RUNTIME_MODE": "demo"}, clear=True):
            with self.assertRaises(RuntimeConfigurationError):
                load_demo_configuration()
        with mock.patch.dict(os.environ, {
            "KCD_RUNTIME_MODE": "demo", "KCD_DEMO_DATA_ROOT": str(Path(tempfile.gettempdir()).resolve()),
            "KCD_STORAGE_BACKEND": "dual", "KCD_REPORTS_BACKEND": "sql",
        }, clear=True):
            with self.assertRaises(RuntimeConfigurationError):
                load_demo_configuration()

    def test_normal_runtime_does_not_initialize_demo_components(self):
        for value in (None, "normal"):
            with self.subTest(runtime=value), tempfile.TemporaryDirectory() as temp:
                demo_root = Path(temp) / "must-not-exist"
                environment = {"KCD_DEMO_DATA_ROOT": str(demo_root)}
                if value is not None:
                    environment["KCD_RUNTIME_MODE"] = value
                with mock.patch.dict(os.environ, environment, clear=True):
                    app = Flask(f"normal-{value}")
                    manager = install_workspace_runtime(app)

                    @app.get("/normal-probe")
                    def normal_probe():
                        return jsonify({"mode": g.workspace_context.runtime_mode})

                    response = app.test_client().get("/normal-probe")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_json(), {"mode": "normal"})
                    self.assertIsNone(manager)
                    self.assertNotIn("kcd_demo_manager", app.extensions)
                    self.assertNotIn(
                        "/demo/foundation/session",
                        {rule.rule for rule in app.url_map.iter_rules()},
                    )
                    self.assertFalse(demo_root.exists())
if __name__ == "__main__":
    unittest.main()

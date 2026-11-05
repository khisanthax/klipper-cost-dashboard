from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from flask import Flask, g, jsonify, render_template_string, request

from core import db as db_module
from core.demo.flask_integration import install_workspace_runtime
from core.demo.config import load_demo_configuration
from core.demo.network import DemoNetworkBlocked, require_outbound_network
from core.demo.manager import SeedValidationError
from core.demo.registry import DemoWorkspaceRegistry
from core.demo.security import (
    APPLICATION_ROUTE_POLICIES, DemoPolicyClass, csrf_token,
)
from core.workspace_context import WorkspaceContext
from core.runtime import RuntimeConfigurationError


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, **kwargs):
        with self.lock:
            self.value += timedelta(**kwargs)


def build_seed(root: Path) -> None:
    seed = root / "seeds" / "basic-v1"
    seed.mkdir(parents=True)
    context = WorkspaceContext(
        "normal", None, seed, seed / "kcd.db", seed / "cache", seed / "exports",
        seed / "temp", seed / "uploads", None, "sql",
    )
    with closing(db_module.connect_db(context)) as conn:
        db_module.apply_migrations(conn)
        conn.execute(
            "INSERT INTO printers(name,created_at,updated_at) VALUES('Seed','x','x')"
        )
        conn.commit()


class DemoSecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo"
        self.root.mkdir()
        build_seed(self.root)
        self.environment = mock.patch.dict(os.environ, {
            "KCD_RUNTIME_MODE": "demo",
            "KCD_DEMO_DATA_ROOT": str(self.root),
            "KCD_STORAGE_BACKEND": "sql",
            "KCD_REPORTS_BACKEND": "sql",
            "KCD_DEMO_COOKIE_SECURE": "0",
        }, clear=False)
        self.environment.start()
        self.app = Flask(f"security-{id(self)}")
        self.manager = install_workspace_runtime(self.app)
        self.clock = MutableClock()
        self.manager.clock = self.clock
        self.manager.registry.clock = self.clock
        self.side_effects = 0

        @self.app.get("/health", endpoint="health")
        def health():
            return jsonify({"unsafe": str(self.root)})

        @self.app.get("/safe")
        def safe():
            return jsonify({"status": "ok"})

        @self.app.get("/form")
        def form():
            return '<form method="post" action="/mutate"><input name="name"></form>'

        @self.app.post("/mutate")
        def mutate():
            action = request.form.get("action", "change")
            with closing(db_module.connect_db()) as conn:
                if action == "add_filament_profile":
                    name = request.form["name"]
                    conn.execute(
                        """INSERT INTO filament_profiles
                           (profile_uid,name,created_at,updated_at) VALUES(?,?,?,?)""",
                        (name, name, "x", "x"),
                    )
                elif action == "add_rate_profile":
                    name = request.form["name"]
                    conn.execute(
                        """INSERT INTO hourly_rate_profiles
                           (profile_uid,name,rate_per_hour,created_at,updated_at)
                           VALUES(?,?,?,?,?)""",
                        (name, name, 1, "x", "x"),
                    )
                elif action == "create_project":
                    name = request.form["name"]
                    conn.execute(
                        "INSERT INTO projects(project_uid,name,created_at,updated_at) VALUES(?,?,?,?)",
                        (name, name, "x", "x"),
                    )
                else:
                    conn.execute(
                        "INSERT INTO printers(name,created_at,updated_at) VALUES(?,?,?)",
                        (request.form["name"], "x", "x"),
                    )
                conn.commit()
            self.side_effects += 1
            return jsonify({"status": "changed"})

        @self.app.post("/json")
        def json_mutate():
            self.side_effects += 1
            return jsonify({"value": request.get_json()})

        @self.app.get("/danger")
        def danger():
            self.side_effects += 1
            socket.getaddrinfo("example.com", 443)
            subprocess.Popen(["network-helper"])
            return "unsafe"

        @self.app.get("/unknown")
        def unknown():
            self.side_effects += 1
            return "unsafe"

        @self.app.get("/explode")
        def explode():
            token = request.cookies.get("kcd_demo_session")
            raise RuntimeError(
                f"token={token} path={g.workspace_context.database_path} SELECT secret FROM registry"
            )

        policy = self.app.extensions["kcd_demo_policy"]
        policy.register("safe", "GET", DemoPolicyClass.WORKSPACE_READ)
        policy.register("form", "GET", DemoPolicyClass.WORKSPACE_READ)
        policy.register(
            "mutate", "POST", DemoPolicyClass.WORKSPACE_MUTATE,
            actions={"change", "add_filament_profile", "add_rate_profile", "create_project"},
        )
        policy.register("json_mutate", "POST", DemoPolicyClass.WORKSPACE_MUTATE)
        policy.register("danger", "GET", DemoPolicyClass.BLOCKED_DEMO)
        policy.register("explode", "GET", DemoPolicyClass.WORKSPACE_READ)
        self.app.testing = True

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        response = client.post("/demo/foundation/session")
        self.assertEqual(response.status_code, 201)
        return client

    @staticmethod
    def csrf(client):
        token = client.get_cookie("kcd_demo_session").value
        return csrf_token(token)

    def post_form(self, client, data, **kwargs):
        payload = dict(data)
        payload["_csrf_token"] = self.csrf(client)
        return client.post("/mutate", data=payload, **kwargs)

    def test_route_policy_is_complete_and_default_deny(self):
        for endpoint, method in (
            ("installer_registration", "GET"),
            ("installer_registration", "POST"),
            ("job_thumbnail", "GET"),
            ("archived_thumbnail", "GET"),
            ("project_pricing_page", "POST"),
        ):
            self.assertEqual(
                APPLICATION_ROUTE_POLICIES[(endpoint, method)].classification,
                DemoPolicyClass.BLOCKED_DEMO,
            )
        missing = self.app.extensions["kcd_demo_policy"].missing_routes(self.app)
        self.assertEqual(missing, [("unknown", "GET", "/unknown")])
        client = self.client()
        self.assertEqual(client.get("/unknown").status_code, 403)
        self.assertEqual(self.side_effects, 0)

    def test_security_configuration_fails_closed(self):
        with mock.patch.dict(os.environ, {"KCD_DEMO_MAX_REQUEST_BYTES": "0"}, clear=False):
            with self.assertRaises(RuntimeConfigurationError):
                load_demo_configuration()
        with mock.patch.dict(os.environ, {"KCD_DEMO_TRUST_PROXY": "sometimes"}, clear=False):
            with self.assertRaises(RuntimeConfigurationError):
                load_demo_configuration()

    def test_new_milestone_surfaces_are_explicitly_denied_before_handlers(self):
        def unavailable(**_values):
            self.side_effects += 1
            socket.getaddrinfo("example.com", 443)
            subprocess.Popen(["forbidden-installer-helper"])
            return "unsafe"

        surfaces = (
            ("/printers/<path:printer_name>", "printer_detail", ("GET",)),
            ("/orders", "orders_page", ("GET", "POST")),
            ("/inventory", "inventory_page", ("GET", "POST")),
            ("/estimates", "estimate_page", ("GET", "POST")),
            ("/api/estimates", "estimate_page", ("POST",)),
            ("/estimates/plan", "save_estimate_plan", ("POST",)),
            ("/projects/<project_uid>/export.json", "export_project", ("GET",)),
        )
        for path, endpoint, methods in surfaces:
            self.app.add_url_rule(path, endpoint, unavailable, methods=methods)
        client = self.client()
        headers = {"X-CSRF-Token": self.csrf(client), "Origin": "http://localhost"}
        with mock.patch("socket.getaddrinfo") as network, mock.patch("subprocess.Popen") as command:
            for path, endpoint, methods in surfaces:
                path = path.replace("<path:printer_name>", "Demo").replace("<project_uid>", "sample")
                for method in methods:
                    with self.subTest(endpoint=endpoint, method=method):
                        rule = self.app.extensions["kcd_demo_policy"].classify(endpoint, method)
                        self.assertEqual(rule.classification, DemoPolicyClass.BLOCKED_DEMO)
                        self.assertEqual(client.open(path, method=method, headers=headers).status_code, 403)
            network.assert_not_called()
            command.assert_not_called()
        self.assertEqual(self.side_effects, 0)
        self.assertEqual(self.app.extensions["kcd_demo_policy"].missing_routes(self.app), [("unknown", "GET", "/unknown")])

    def test_template_availability_uses_the_same_endpoint_method_and_action_policy(self):
        @self.app.get("/availability")
        def availability():
            return render_template_string(
                "{% if demo_endpoint_available('safe') %}Workspace{% endif %}"
                "{% if demo_endpoint_available('inventory_page') %}Inventory{% endif %}"
                "{% if demo_endpoint_available('unknown') %}Unknown{% endif %}"
                "{% if demo_endpoint_available('settings_other_page','POST','update_application_mode') %}Mode{% endif %}"
                "{% if demo_endpoint_available('projects_page','POST','create_project') %}Sample project{% endif %}"
                "{% if demo_endpoint_available('settings_profiles_page','POST','add_filament_profile') %}Sample profile{% endif %}"
                "{% if demo_endpoint_available('recalculate_run','POST') %}Sample pricing{% endif %}"
            )

        self.app.extensions["kcd_demo_policy"].register("availability", "GET", DemoPolicyClass.WORKSPACE_READ)
        response = self.client().get("/availability")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "WorkspaceSample projectSample profileSample pricing")

    def test_host_and_trusted_proxy_requirements(self):
        from core.demo.security import DemoSecurityError, validate_transport
        configuration = replace(self.manager.configuration, expect_https=True, trust_proxy=True)
        cases = [
            ("http://localhost", {}, "127.0.0.1", False),
            ("https://evil.example", {}, "127.0.0.1", False),
            ("http://localhost", {"X-Forwarded-Proto": "https"}, "192.0.2.20", False),
            ("http://localhost", {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"}, "127.0.0.1", False),
            ("http://localhost", {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "localhost"}, "127.0.0.1", True),
            ("https://localhost", {}, "192.0.2.20", True),
        ]
        for base, headers, address, allowed in cases:
            with self.subTest(base=base, headers=headers, address=address), self.app.test_request_context(
                "/", base_url=base, headers=headers, environ_overrides={"REMOTE_ADDR": address}
            ):
                if allowed:
                    validate_transport(request, configuration)
                else:
                    with self.assertRaises(DemoSecurityError):
                        validate_transport(request, configuration)

    def test_nested_and_wide_requests_are_bounded(self):
        client = self.client()
        headers = {"X-CSRF-Token": self.csrf(client)}
        payload = {}
        for _ in range(10):
            payload = {"child": payload}
        self.assertEqual(client.post("/json", json=payload, headers=headers).status_code, 422)
        self.assertEqual(client.post("/json", json={"rate": 10 ** 500}, headers=headers).status_code, 422)
        data = {f"field-{index}": "x" for index in range(self.manager.configuration.max_list_length + 1)}
        self.assertEqual(client.post("/mutate", data=data, headers=headers).status_code, 422)

    def test_normal_mode_bypasses_demo_policy_network_limits_and_configuration(self):
        with mock.patch.dict(os.environ, {
            "KCD_RUNTIME_MODE": "normal",
            "KCD_DEMO_MAX_REQUEST_BYTES": "invalid-in-normal-mode",
        }, clear=False):
            app = Flask("normal-boundary-bypass")

            @app.get("/ordinary")
            def ordinary():
                require_outbound_network()
                return "normal"

            self.assertIsNone(install_workspace_runtime(app))
            self.assertEqual(app.test_client().get("/ordinary").get_data(as_text=True), "normal")

    def test_allowed_reads_forms_and_method_safety(self):
        client = self.client()
        self.assertEqual(client.get("/safe").status_code, 200)
        form = client.get("/form").get_data(as_text=True)
        self.assertIn('name="_csrf_token"', form)
        self.assertIn(self.csrf(client), form)
        self.assertEqual(client.get("/mutate").status_code, 403)
        self.assertEqual(self.side_effects, 0)

    def test_csrf_session_binding_and_origin(self):
        a = self.client()
        b = self.client()
        self.assertEqual(a.post("/mutate", data={"action": "change", "name": "missing"}).status_code, 403)
        self.assertEqual(
            a.post(
                "/mutate",
                data={"action": "change", "name": "wrong", "_csrf_token": self.csrf(b)},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.post_form(
                a, {"action": "change", "name": "cross"},
                headers={"Origin": "https://evil.example"},
            ).status_code,
            403,
        )
        response = self.post_form(
            a, {"action": "change", "name": "Unicode π"},
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.side_effects, 1)

    def test_input_and_upload_limits_fail_before_side_effects(self):
        client = self.client()
        self.assertEqual(
            client.post(
                "/json", data="{", content_type="application/json",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/json", json={"callback_url": "http://127.0.0.1/"},
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.post_form(client, {"action": "change", "name": "x" * 257}).status_code,
            422,
        )
        self.assertEqual(
            self.post_form(client, {"action": "change", "name": "NaN"}).status_code,
            422,
        )
        self.assertEqual(
            client.post(
                "/json", data=b"x" * (1024 * 1024 + 1),
                content_type="application/json",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            413,
        )
        self.assertEqual(
            client.post(
                "/mutate", data={"action": "change", "file": (b"data", "x.txt")},
                content_type="multipart/form-data",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            403,
        )
        self.assertEqual(self.side_effects, 0)

    def test_blocked_route_makes_zero_network_or_subprocess_calls(self):
        client = self.client()
        with mock.patch("socket.getaddrinfo") as dns, mock.patch("subprocess.Popen") as popen:
            response = client.get("/danger")
        self.assertEqual(response.status_code, 403)
        dns.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(self.side_effects, 0)

    def test_rate_limits_are_scoped_and_roll_over(self):
        self.manager.configuration = replace(
            self.manager.configuration,
            rate_general_limit=2,
            rate_mutation_limit=1,
            rate_invalid_session_limit=3,
        )
        a = self.client()
        b = self.client()
        self.assertEqual(a.get("/safe").status_code, 200)
        self.assertEqual(a.get("/safe").status_code, 200)
        limited = a.get("/safe")
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        self.assertEqual(b.get("/safe").status_code, 200)
        self.clock.advance(minutes=6)
        self.assertEqual(a.get("/safe").status_code, 200)

    def test_mutation_lifecycle_and_invalid_session_rate_classes(self):
        self.manager.configuration = replace(
            self.manager.configuration,
            rate_general_limit=100,
            rate_mutation_limit=1,
            rate_lifecycle_limit=1,
            rate_invalid_session_limit=3,
        )
        client = self.client()
        self.assertEqual(
            self.post_form(client, {"action": "change", "name": "first"}).status_code,
            200,
        )
        self.assertEqual(
            self.post_form(client, {"action": "change", "name": "second"}).status_code,
            429,
        )
        self.assertEqual(
            client.post(
                "/demo/foundation/reset",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post(
                "/demo/foundation/reset",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            429,
        )
        stranger = self.app.test_client()
        self.assertEqual(stranger.get("/unknown").status_code, 403)
        self.assertEqual(stranger.get("/unknown").status_code, 403)
        self.assertEqual(stranger.get("/unknown").status_code, 429)

    def test_network_boundary_blocks_direct_outbound_use(self):
        with self.assertRaises(DemoNetworkBlocked):
            require_outbound_network()

    def test_quota_reservations_and_reset(self):
        self.manager.configuration = replace(
            self.manager.configuration,
            max_filament_profiles=1,
            max_hourly_profiles=1,
            max_projects=1,
        )
        client = self.client()
        for action in ("add_filament_profile", "add_rate_profile", "create_project"):
            self.assertEqual(
                self.post_form(client, {"action": action, "name": action}).status_code,
                200,
            )
            self.assertEqual(
                self.post_form(client, {"action": action, "name": action + "2"}).status_code,
                409,
            )
        self.assertEqual(
            client.post(
                "/demo/foundation/reset",
                headers={"X-CSRF-Token": self.csrf(client)},
            ).status_code,
            200,
        )

        other = self.client()
        self.assertEqual(
            self.post_form(other, {"action": "create_project", "name": "other-workspace"}).status_code,
            200,
        )

    def test_concurrent_near_limit_cannot_bypass_quota(self):
        self.manager.configuration = replace(self.manager.configuration, max_projects=1)
        first = self.client()
        second = self.app.test_client()
        second.set_cookie("kcd_demo_session", first.get_cookie("kcd_demo_session").value)
        barrier = threading.Barrier(2)

        def create(client, name):
            barrier.wait(timeout=2)
            return self.post_form(client, {"action": "create_project", "name": name}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: create(*args), ((first, "one"), (second, "two"))))
        self.assertEqual(sorted(results), [200, 409])

    def test_storage_quota_is_applied_to_workspace_connections(self):
        client = self.client()
        token = client.get_cookie("kcd_demo_session").value
        session = self.manager.resolve_workspace(token)
        with closing(db_module.connect_db(session.context)) as conn:
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            max_pages = int(conn.execute("PRAGMA max_page_count").fetchone()[0])
        self.assertLessEqual(max_pages * page_size, self.manager.configuration.max_workspace_bytes)

    def test_seed_and_mutation_storage_limits_fail_closed(self):
        seed_bytes = sum(
            path.stat().st_size
            for path in self.manager.configuration.seed_root.rglob("*")
            if path.is_file()
        )
        self.manager.configuration = replace(
            self.manager.configuration, max_workspace_bytes=seed_bytes - 1
        )
        with self.assertRaises(SeedValidationError):
            self.manager.validate_seed()

        self.manager.configuration = replace(
            self.manager.configuration, max_workspace_bytes=25 * 1024 * 1024
        )
        client = self.client()
        token = client.get_cookie("kcd_demo_session").value
        session = self.manager.resolve_workspace(token)
        current_bytes = self.manager.workspace_storage_bytes(session.context)
        self.manager.configuration = replace(
            self.manager.configuration, max_workspace_bytes=current_bytes
        )
        response = self.post_form(client, {"action": "change", "name": "blocked"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.side_effects, 0)

    def test_error_redaction_and_security_headers(self):
        client = self.client()
        token = client.get_cookie("kcd_demo_session").value
        session = self.manager.resolve_workspace(token)
        with self.assertLogs("core.demo.flask_integration", level="ERROR") as logs:
            response = client.get("/explode", headers={"Accept": "application/json"})
        body = response.get_data(as_text=True)
        log_body = "\n".join(logs.output)
        for secret in (
            token,
            self.manager.hash_token(token),
            session.record.workspace_id,
            str(session.context.database_path),
            "SELECT secret",
        ):
            self.assertNotIn(secret, body)
            self.assertNotIn(secret, log_body)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        html = client.get("/explode").get_data(as_text=True)
        self.assertNotIn(token, html)
        self.assertNotIn(str(session.context.database_path), html)
        self.manager.configuration = replace(self.manager.configuration, expect_https=True)
        self.assertIn("max-age=", client.get("/safe").headers["Strict-Transport-Security"])

    def test_health_is_minimal(self):
        response = self.app.test_client().get("/health")
        self.assertEqual(response.get_json(), {"status": "available"})
        self.assertNotIn(str(self.root), response.get_data(as_text=True))

    def test_actual_application_allowlist_and_blocked_action(self):
        code = (
            "import json\n"
            "from contextlib import closing\n"
            "from unittest import mock\n"
            "import app as target\n"
            "assert not target.API_KEY\n"
            "from core import db as db_module\n"
            "from core.demo.security import csrf_token\n"
            "client=target.app.test_client()\n"
            "created=client.post('/demo/foundation/session')\n"
            "raw=client.get_cookie('kcd_demo_session').value\n"
            "headers={'X-CSRF-Token': csrf_token(raw)}\n"
            "pages={path:client.get(path) for path in ('/','/reports','/projects','/settings/profiles','/settings/printers','/settings/other')}\n"
            "reads=[pages[path].status_code for path in ('/','/reports','/projects','/settings/profiles')]\n"
            "ui={'moonraker':b'Moonraker URL' in pages['/settings/printers'].data,'diagnostics':b'Diagnostics' in pages['/settings/printers'].data,'backup':b'Backup now' in pages['/settings/other'].data,'upload':b'<input type=\"file\"' in pages['/projects'].data}\n"
            "with mock.patch.object(target, 'create_backup_archive', side_effect=AssertionError('route ran')) as blocked:\n"
            "    danger=client.post('/settings/other', data={'action':'backup_now'}, headers=headers)\n"
            "with mock.patch.object(target.storage_backend, 'write_job', side_effect=AssertionError('route ran')) as write_job:\n"
            "    printer=client.post('/log-print', json={'printer':'x'}, headers={'X-API-Key':'secret','X-CSRF-Token':csrf_token(raw)})\n"
            "download=client.get('/download-csv?path=../../outside')\n"
            "new_denied=[client.get(path).status_code for path in ('/printers/Demo','/orders','/inventory','/estimates','/projects/sample/export.json')]\n"
            "new_denied.extend(client.post(path,headers=headers).status_code for path in ('/orders','/inventory','/estimates','/api/estimates','/estimates/plan'))\n"
            "mode=client.post('/settings/other',data={'action':'update_application_mode','mode':'business'},headers=headers)\n"
            "safe=client.post('/settings/profiles', data={'action':'add_filament_profile','profile_name':'Phase4','filament_mode':'per_kg','filament_rate':'20','grams_per_meter':'3'}, headers=headers)\n"
            "session=target.app.extensions['kcd_demo_manager'].resolve_workspace(raw)\n"
            "with closing(db_module.connect_db(session.context)) as conn:\n"
            "    count=conn.execute(\"SELECT COUNT(*) FROM filament_profiles WHERE name='Phase4'\").fetchone()[0]\n"
            "print(json.dumps({'created':created.status_code,'reads':reads,'ui':ui,'blocked':danger.status_code,'blocked_calls':blocked.call_count,'printer':printer.status_code,'printer_calls':write_job.call_count,'download':download.status_code,'new_denied':new_denied,'mode':mode.status_code,'safe':safe.status_code,'count':count,'missing':target.app.extensions['kcd_demo_policy'].missing_routes(target.app)}))\n"
        )
        environment = os.environ.copy()
        environment["KCD_API_KEY"] = "fixture-must-not-be-loaded"
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(Path(__file__).resolve().parents[1]), environment.get("PYTHONPATH", ""),
        )))
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.tmp.name,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result, {
            "created": 201,
            "reads": [200, 200, 200, 200],
            "ui": {"moonraker": False, "diagnostics": False, "backup": False, "upload": False},
            "blocked": 403,
            "blocked_calls": 0,
            "printer": 403,
            "printer_calls": 0,
            "download": 403,
            "new_denied": [403] * 10,
            "mode": 403,
            "safe": 302,
            "count": 1,
            "missing": [],
        })


class CrossProcessRateLimitTests(unittest.TestCase):
    def test_registry_rate_limit_is_atomic_across_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.db"
            now = "2026-08-26T16:00:00+00:00"
            registry = DemoWorkspaceRegistry(path, lambda: datetime.fromisoformat(now))
            registry.initialize()
            code = (
                "from datetime import datetime\n"
                "from pathlib import Path\n"
                "import os\n"
                "from core.demo.registry import DemoWorkspaceRegistry\n"
                "now=datetime.fromisoformat(os.environ['RATE_NOW'])\n"
                "r=DemoWorkspaceRegistry(Path(os.environ['RATE_DB']), lambda: now)\n"
                "print(sum(r.consume_rate('same','general',limit=7,window_seconds=300,now=now)[0] for _ in range(5)))\n"
            )
            environment = os.environ.copy()
            environment["RATE_DB"] = str(path)
            environment["RATE_NOW"] = now
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
                str(Path(__file__).resolve().parents[1]), environment.get("PYTHONPATH", ""),
            )))

            def run():
                return subprocess.run(
                    [sys.executable, "-c", code],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    env=environment,
                    check=True,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                completed = list(pool.map(lambda _value: run(), range(2)))
            self.assertEqual(sum(int(result.stdout.strip()) for result in completed), 7)


if __name__ == "__main__":
    unittest.main()

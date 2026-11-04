import base64
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from installer import web_admin


PASSWORD = "separate-fixture-admin-password"
MASTER_KEY = "fixture-printer-key"
AUTH = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()


class WebAdminTests(unittest.TestCase):
    def setUp(self):
        self.target = web_admin.Target("SV07", "/tmp/fixture", "http://127.0.0.1:7125")
        self.app = web_admin.create_app("http://127.0.0.1:6060", {"sv07": self.target}, PASSWORD, MASTER_KEY)
        self.client = self.app.test_client()
        self.headers = {"Authorization": AUTH, "Origin": "http://localhost"}

    def token(self, response, name):
        return re.search(f'name="{name}" value="([^"]+)"', response.get_data(as_text=True))[1]

    def test_admin_requires_separate_credential_and_rejects_untrusted_host(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        key_auth = "Basic " + base64.b64encode(f"admin:{MASTER_KEY}".encode()).decode()
        self.assertEqual(self.client.get("/", headers={"Authorization": key_auth}).status_code, 401)
        self.assertEqual(self.client.get("/", headers={**self.headers, "Host": "evil.invalid"}).status_code, 400)
        with self.assertRaises(ValueError):
            web_admin.create_app("http://localhost", {}, PASSWORD, PASSWORD)

    def test_apply_requires_origin_csrf_and_signed_preview(self):
        csrf = self.token(self.client.get("/", headers=self.headers), "csrf")
        with patch.object(web_admin, "repair_client") as engine:
            self.assertEqual(self.client.post("/apply", headers=self.headers, data={"ticket": "fake"}).status_code, 403)
            self.assertEqual(self.client.post("/preview", headers={**self.headers, "Origin": "http://localhost:9999"},
                                              data={"csrf": csrf, "target": "sv07"}).status_code, 403)
            self.assertEqual(self.client.post("/apply", headers=self.headers,
                                              data={"csrf": csrf, "ticket": "fake"}).status_code, 409)
            engine.assert_not_called()

    def test_review_binds_target_and_engine_revision(self):
        csrf = self.token(self.client.get("/", headers=self.headers), "csrf")
        with patch.object(web_admin, "repair_client", side_effect=[
            {"applied": False, "changes": ["create print_cost.cfg"], "revision": "fixture-revision"},
            {"applied": True},
        ]) as engine:
            preview = self.client.post("/preview", headers=self.headers, data={
                "csrf": csrf, "target": "sv07", "config_dir": "/arbitrary", "key": "browser-key",
            })
            self.assertEqual(preview.status_code, 200)
            self.assertNotIn(MASTER_KEY.encode(), preview.data)
            ticket = self.token(preview, "ticket")
            result = self.client.post("/apply", headers=self.headers, data={"csrf": csrf, "ticket": ticket})
            self.assertIn(b"Installation verified for SV07", result.data)
            self.assertEqual(engine.call_args.kwargs["expected_revision"], "fixture-revision")
            self.assertEqual(engine.call_args.kwargs["config_dir"], self.target.config_dir)
            self.assertEqual(engine.call_args.kwargs["key"], MASTER_KEY)
            self.assertTrue(engine.call_args.kwargs["web_safe"])
            self.assertEqual(self.client.post("/apply", headers=self.headers,
                                              data={"csrf": csrf, "ticket": ticket}).status_code, 409)

    def test_ssh_uses_only_configured_alias_and_hides_remote_error(self):
        target = web_admin.Target("SV07", "/home/fixture/config", "http://127.0.0.1:7125", "approved-alias")
        client = web_admin.create_app("http://localhost", {"sv07": target}, PASSWORD, MASTER_KEY).test_client()
        csrf = self.token(client.get("/", headers=self.headers), "csrf")
        with patch.object(web_admin, "remote_operation", side_effect=RuntimeError(MASTER_KEY)) as remote:
            result = client.post("/preview", headers=self.headers, data={
                "csrf": csrf, "target": "sv07", "ssh_alias": "attacker",
            })
        self.assertEqual(result.status_code, 409)
        self.assertNotIn(MASTER_KEY.encode(), result.data)
        host, operation, payload = remote.call_args.args
        self.assertEqual((host, operation), ("approved-alias", "repair"))
        self.assertEqual(payload["config_dir"], target.config_dir)

    def test_allowlist_rejects_paths_and_endpoints_with_unsafe_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.json"
            entry = {"name": "SV07", "ssh_alias": "printer", "config_dir": "/home/operator/config",
                     "moonraker": "http://192.168.0.10:7125"}
            for invalid in ({"config_dir": "/home/operator/../config"}, {"ssh_alias": "-oProxyCommand"},
                            {"moonraker": "http://password@192.168.0.10"}):
                path.write_text(json.dumps({"master": "http://localhost", "targets": {"p": {**entry, **invalid}}}))
                with self.assertRaises(ValueError):
                    web_admin.load_configuration(path)

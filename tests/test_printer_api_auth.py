import os
import sys
import unittest
from unittest.mock import patch


class PrinterApiAuthenticationTests(unittest.TestCase):
    ENDPOINTS = (
        "/log-print",
        "/job-start",
        "/job-update",
        "/job-pause",
        "/job-resume",
        "/job-cancel",
        "/job-end",
    )

    @classmethod
    def setUpClass(cls):
        cls._previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "csv"
        sys.modules.pop("app", None)
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("app", None)
        if cls._previous_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = cls._previous_backend

    def test_correct_key_reaches_every_endpoint(self):
        with patch.object(self.app_module, "API_KEY", "server-secret"):
            for endpoint in self.ENDPOINTS:
                with self.subTest(endpoint=endpoint):
                    response = self.client.post(
                        endpoint,
                        json={},
                        headers={"X-API-Key": "server-secret"},
                    )
                    self.assertNotIn(response.status_code, (403, 503))

    def test_missing_key_is_rejected_by_every_endpoint(self):
        with patch.object(self.app_module, "API_KEY", "server-secret"):
            for endpoint in self.ENDPOINTS:
                with self.subTest(endpoint=endpoint):
                    response = self.client.post(endpoint, json={})
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.get_json()["error"], "Unauthorized")

    def test_incorrect_key_is_rejected_by_every_endpoint(self):
        with patch.object(self.app_module, "API_KEY", "server-secret"):
            for endpoint in self.ENDPOINTS:
                with self.subTest(endpoint=endpoint):
                    response = self.client.post(
                        endpoint,
                        json={},
                        headers={"X-API-Key": "wrong-secret"},
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.get_json()["error"], "Unauthorized")

    def test_missing_server_key_fails_closed_for_every_endpoint(self):
        with patch.object(self.app_module, "API_KEY", None):
            for endpoint in self.ENDPOINTS:
                with self.subTest(endpoint=endpoint):
                    response = self.client.post(
                        endpoint,
                        json={},
                        headers={"X-API-Key": "anything"},
                    )
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("not configured", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

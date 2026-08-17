import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


class ReadinessNumericValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from core import db as db_module

        self.db_module = db_module
        self._original_db_path = db_module._db_path
        db_module._db_path = lambda: os.path.join(self._tmp.name, "kcd.db")
        with closing(db_module.connect_db()) as conn:
            db_module.apply_migrations(conn)
            db_module.upsert_printer(conn, "SV08")
            self._save(conn, "display_settings", {"pause_include_paused_time_default": False})

    def tearDown(self):
        self.db_module._db_path = self._original_db_path
        self._tmp.cleanup()

    def _save(self, conn, key, value):
        conn.execute(
            """
            INSERT INTO user_settings (key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def _set_pricing(self, *, rate=1.0, filament_rate=1.0, grams=3.0):
        with closing(self.db_module.connect_db()) as conn:
            self._save(
                conn,
                "printer_settings",
                {
                    "SV08": {
                        "rate_per_hour": rate,
                        "filament_mode": "per_meter",
                        "filament_rate": filament_rate,
                        "grams_per_meter": grams,
                    }
                },
            )

    def test_readiness_rejects_nonfinite_negative_and_zero_invalid_pricing(self):
        from core.readiness import check_sql_only_readiness

        cases = (
            {"rate": "nan"},
            {"rate": "inf"},
            {"rate": "-inf"},
            {"rate": -1},
            {"filament_rate": "nan"},
            {"filament_rate": -1},
            {"grams": "inf"},
            {"grams": -1},
            {"grams": 0},
        )
        for values in cases:
            with self.subTest(values=values):
                self._set_pricing(**values)
                readiness = check_sql_only_readiness()
                self.assertFalse(readiness["ready"])
                self.assertTrue(
                    any(error.get("code") == "invalid_printer_pricing_config" for error in readiness["errors"])
                )

    def test_readiness_allows_legitimate_zero_cost_pricing(self):
        from core.readiness import check_sql_only_readiness

        self._set_pricing(rate=0, filament_rate=0, grams=3)
        self.assertTrue(check_sql_only_readiness()["ready"])


class RuntimeNumericValidationTests(unittest.TestCase):
    def test_pricing_rejects_invalid_measurements_and_config(self):
        from core import pricing
        from core.numeric import NumericValidationError

        patches = (
            patch.object(pricing, "get_effective_rate_per_hour", return_value=1.0),
            patch.object(
                pricing,
                "_get_effective_filament_pricing",
                return_value={"filament_mode": "per_meter", "filament_rate": 1.0, "grams_per_meter": 3.0},
            ),
            patch.object(pricing, "_include_paused_time_for_printer", return_value=True),
            patch.object(pricing.profiles, "get_printer_mapping", return_value=None),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            for field, value in (
                ("duration", "nan"),
                ("duration", -1),
                ("filament", "inf"),
                ("filament", -1),
                ("paused", "-inf"),
                ("paused", -1),
            ):
                args = {"duration": 1, "filament": 1, "paused": 0}
                args[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(NumericValidationError):
                    pricing.compute_costs("SV08", args["duration"], args["filament"], args["paused"])

        with patch.object(pricing, "get_effective_rate_per_hour", return_value=float("inf")), patch.object(
            pricing.profiles, "get_printer_mapping", return_value=None
        ):
            with self.assertRaises(NumericValidationError):
                pricing.compute_costs("SV08", 1, 1)

    def test_pricing_allows_zero_cost_rates(self):
        from core import pricing

        with patch.object(pricing, "get_effective_rate_per_hour", return_value=0.0), patch.object(
            pricing,
            "_get_effective_filament_pricing",
            return_value={"filament_mode": "per_meter", "filament_rate": 0.0, "grams_per_meter": 3.0},
        ), patch.object(pricing, "_include_paused_time_for_printer", return_value=True), patch.object(
            pricing.profiles, "get_printer_mapping", return_value=None
        ):
            result = pricing.compute_costs("SV08", 3600, 1000)
        self.assertEqual(result["total_cost"], 0.0)


class RouteNumericValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "csv"
        sys.modules.pop("app", None)
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()
        cls.headers = {"X-API-Key": "numeric-secret"}

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("app", None)
        if cls._previous_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = cls._previous_backend

    def test_log_print_rejects_nonfinite_and_negative_payload_values(self):
        base = {
            "timestamp": 1_700_000_000,
            "printer": "SV08",
            "filename": "part.gcode",
            "duration_seconds": 1,
            "filament_mm": 1,
        }
        with patch.object(self.app_module, "API_KEY", "numeric-secret"):
            for field, value in (
                ("timestamp", "nan"),
                ("timestamp", "inf"),
                ("duration_seconds", -1),
                ("duration_seconds", "-inf"),
                ("filament_mm", -1),
                ("filament_mm", "inf"),
            ):
                payload = dict(base)
                payload[field] = value
                with self.subTest(field=field, value=value):
                    response = self.client.post("/log-print", json=payload, headers=self.headers)
                    self.assertEqual(response.status_code, 400)

    def test_live_routes_reject_invalid_measurements_and_timestamps(self):
        with patch.object(self.app_module, "API_KEY", "numeric-secret"), patch.object(
            self.app_module, "get_canonical_printer_names", return_value={"SV08"}
        ):
            cases = (
                ("/job-start", {"printer_name": "SV08", "filename": "part.gcode", "start_time": "nan"}),
                ("/job-start", {"printer_name": "SV08", "filename": "part.gcode", "estimated_duration": -1}),
                ("/job-start", {"printer_name": "SV08", "filename": "part.gcode", "estimated_filament_mm": "inf"}),
                ("/job-update", {"printer_name": "SV08", "estimated_duration": "-inf"}),
                ("/job-update", {"printer_name": "SV08", "estimated_filament_mm": -1}),
                ("/job-cancel", {"printer_name": "SV08", "filename": "part.gcode", "elapsed_seconds": "nan"}),
                ("/job-cancel", {"printer_name": "SV08", "filename": "part.gcode", "elapsed_seconds": -1}),
            )
            for endpoint, payload in cases:
                with self.subTest(endpoint=endpoint, payload=payload):
                    response = self.client.post(endpoint, json=payload, headers=self.headers)
                    self.assertEqual(response.status_code, 400)

    def test_recalculate_and_sql_forms_reject_nonfinite_or_zero_invalid_values(self):
        with patch.object(self.app_module, "_load_history_rows_for_recalc", return_value=([], None)):
            response = self.client.post(
                "/recalculate/run",
                data={"recompute_mode": "pricing_only", "rate_per_hour_override": "nan"},
            )
        query = parse_qs(urlparse(response.location).query)
        self.assertIn("Invalid hourly rate override", query["msg"][0])

        with patch.object(self.app_module, "_is_sql_only", return_value=True), patch.object(
            self.app_module, "_sql_only_printer_exists", return_value=True
        ):
            response = self.client.post(
                "/settings/printers",
                data={
                    "action": "save_printer_defaults",
                    "printer": "SV08",
                    "rate_per_hour": "nan",
                    "filament_mode": "per_meter",
                    "filament_rate": "1",
                    "grams_per_meter": "3",
                },
            )
            query = parse_qs(urlparse(response.location).query)
            self.assertIn("Invalid hourly rate", query["error"][0])

            response = self.client.post(
                "/settings/profiles",
                data={
                    "action": "add_filament_profile",
                    "profile_name": "Invalid",
                    "filament_mode": "per_meter",
                    "filament_rate": "1",
                    "grams_per_meter": "0",
                },
            )
            query = parse_qs(urlparse(response.location).query)
            self.assertIn("greater than zero", query["error"][0])


if __name__ == "__main__":
    unittest.main()

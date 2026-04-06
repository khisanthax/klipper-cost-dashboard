import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch


class SqlOnlyConfigTests(unittest.TestCase):
    def setUp(self):
        from core import db as db_module

        self._db_module = db_module
        self._prev_backend = os.environ.get("KCD_STORAGE_BACKEND")
        os.environ["KCD_STORAGE_BACKEND"] = "sql"
        data_root = os.path.join(os.getcwd(), "data")
        os.makedirs(data_root, exist_ok=True)
        self._test_id = uuid.uuid4().hex
        self._db_file = os.path.join(data_root, f"test_sql_only_{self._test_id}.db")
        self._orig_db_path = db_module._db_path
        db_module._db_path = lambda: self._db_file

    def tearDown(self):
        self._db_module._db_path = self._orig_db_path
        if self._prev_backend is None:
            os.environ.pop("KCD_STORAGE_BACKEND", None)
        else:
            os.environ["KCD_STORAGE_BACKEND"] = self._prev_backend
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self._db_file + suffix)
            except (FileNotFoundError, PermissionError):
                pass

    def _connect(self):
        conn = self._db_module.connect_db()
        self._db_module.apply_migrations(conn)
        return conn

    def _upsert_user_setting(self, key, value):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), now),
            )
            conn.commit()

    def _redirect_error(self, response):
        location = response.headers.get("Location", "")
        query = parse_qs(urlparse(location).query)
        return (query.get("error") or [""])[0]

    def test_load_settings_sql_only_uses_user_settings_without_disk_probe(self):
        from core import storage

        settings_path = os.path.join(os.getcwd(), "data", f"settings_{self._test_id}.json")
        self._upsert_user_setting("printer_settings", {"SV08": {"rate_per_hour": 12.5}})

        orig_exists = storage.os.path.exists

        def guarded_exists(path):
            if os.path.abspath(path) == os.path.abspath(settings_path):
                raise AssertionError("SQL-only load_settings should not probe settings.json")
            return orig_exists(path)

        with patch.object(storage.os.path, "exists", side_effect=guarded_exists):
            loaded = storage.load_settings(settings_path)

        self.assertEqual(loaded.get("SV08", {}).get("rate_per_hour"), 12.5)

    def test_load_display_settings_sql_only_normalizes_db_value_without_disk_probe(self):
        from core import storage
        from core.config import HEADERS

        display_path = os.path.join(os.getcwd(), "data", f"display_{self._test_id}.json")
        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "hidden_printers": ["SV08"],
                "pause_exclude_paused_time_default": True,
            },
        )

        orig_exists = storage.os.path.exists

        def guarded_exists(path):
            if os.path.abspath(path) == os.path.abspath(display_path):
                raise AssertionError("SQL-only load_display_settings should not probe display.json")
            return orig_exists(path)

        with patch.object(storage.os.path, "exists", side_effect=guarded_exists):
            loaded = storage.load_display_settings(display_path, HEADERS)

        self.assertFalse(loaded.get("pause_include_paused_time_default", True))
        self.assertEqual(loaded.get("hidden_printers"), ["SV08"])
        self.assertIn("history", loaded.get("tables") or {})
        self.assertIn("printer", (loaded.get("tables") or {}).get("history", {}).get("visible_columns", []))

    def test_compute_costs_sql_only_uses_db_backed_pricing_and_pause_settings(self):
        from core import pricing

        self._upsert_user_setting(
            "printer_settings",
            {
                "SV08": {
                    "rate_per_hour": 7.0,
                    "active_rate_profile_id": "rate-fast",
                    "pause_include_paused_time_override_enabled": True,
                    "pause_include_paused_time_override_value": True,
                }
            },
        )
        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": False,
            },
        )
        self._upsert_user_setting("filament_mappings", {"SV08": "pla-red"})

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rate-fast", "Fast Rate", "", 12.0, now, now),
            )
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("pla-red", "PLA Red", "PLA", "per_meter", 2.5, None, 3.0, now, now),
            )
            conn.commit()

        self.assertAlmostEqual(pricing.get_effective_rate_per_hour("SV08"), 12.0, places=6)
        filament = pricing._get_effective_filament_pricing("SV08")
        self.assertEqual(filament.get("filament_mode"), "per_meter")
        self.assertAlmostEqual(float(filament.get("filament_rate") or 0.0), 2.5, places=6)
        self.assertAlmostEqual(float(filament.get("grams_per_meter") or 0.0), 3.0, places=6)

        out = pricing.compute_costs("SV08", 7200.0, 1000.0, paused_seconds_total=3600.0)
        self.assertAlmostEqual(float(out.get("time_cost") or 0.0), 24.0, places=6)
        self.assertAlmostEqual(float(out.get("material_cost") or 0.0), 2.5, places=6)
        self.assertEqual(out.get("filament_profile_id"), "pla-red")
        self.assertEqual(out.get("filament_material"), "PLA")

    def test_compute_costs_sql_only_raises_when_required_pricing_config_missing(self):
        from core import pricing

        with self.assertRaises(pricing.SqlOnlyPricingConfigError):
            pricing.compute_costs("SV08", 3600.0, 1000.0)

    def test_compute_costs_sql_only_raises_when_pause_policy_missing(self):
        from core import pricing

        self._upsert_user_setting(
            "printer_settings",
            {
                "SV08": {
                    "rate_per_hour": 7.0,
                    "filament_mode": "per_meter",
                    "filament_rate": 2.5,
                    "grams_per_meter": 3.0,
                }
            },
        )

        with self.assertRaises(pricing.SqlOnlyPricingConfigError):
            pricing.compute_costs("SV08", 3600.0, 1000.0)

    def test_compute_costs_sql_only_override_bypasses_missing_global_pause_policy(self):
        from core import pricing

        self._upsert_user_setting(
            "printer_settings",
            {
                "SV08": {
                    "rate_per_hour": 7.0,
                    "filament_mode": "per_meter",
                    "filament_rate": 2.5,
                    "grams_per_meter": 3.0,
                    "pause_include_paused_time_override_enabled": True,
                    "pause_include_paused_time_override_value": True,
                }
            },
        )

        out = pricing.compute_costs("SV08", 7200.0, 1000.0, paused_seconds_total=3600.0)

        self.assertAlmostEqual(float(out.get("time_cost") or 0.0), 14.0, places=6)

    def test_compute_costs_with_overrides_supports_sql_only_profile_overrides(self):
        from core import pricing

        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "pause_include_paused_time_default": False,
            },
        )

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rate-override", "Override Rate", "", 15.0, now, now),
            )
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("petg-blue", "PETG Blue", "PETG", "per_meter", 3.25, None, 4.5, now, now),
            )
            conn.commit()

        out = pricing.compute_costs_with_overrides(
            "SV08",
            7200.0,
            1000.0,
            rate_profile_id="rate-override",
            filament_profile_id="petg-blue",
        )

        self.assertAlmostEqual(float(out.get("rate_per_hour") or 0.0), 15.0, places=6)
        self.assertEqual(out.get("filament_mode"), "per_meter")
        self.assertAlmostEqual(float(out.get("filament_rate") or 0.0), 3.25, places=6)
        self.assertAlmostEqual(float(out.get("grams_per_meter") or 0.0), 4.5, places=6)
        self.assertEqual(out.get("filament_profile_id"), "petg-blue")
        self.assertEqual(out.get("filament_material"), "PETG")

    def test_get_discovered_printers_sql_only_avoids_csv_reads(self):
        from core import pricing
        from core.config import CSV_FILE

        self._upsert_user_setting("printer_settings", {"Configured": {"rate_per_hour": 1.0}})
        self._upsert_user_setting(
            "display_settings",
            {
                "visible_columns": ["printer"],
                "hidden_printers": ["Hidden"],
            },
        )

        with self._connect() as conn:
            self._db_module.upsert_printer(conn, "Configured")
            self._db_module.upsert_printer(conn, "Discovered")
            self._db_module.upsert_printer(conn, "Hidden")
            conn.commit()

        orig_exists = pricing.os.path.exists

        def guarded_exists(path):
            if os.path.abspath(path) == os.path.abspath(CSV_FILE):
                raise AssertionError("SQL-only get_discovered_printers should not probe print_costs.csv")
            return orig_exists(path)

        with patch.object(pricing.os.path, "exists", side_effect=guarded_exists):
            discovered = pricing.get_discovered_printers()

        self.assertEqual(discovered, ["Discovered"])

    def test_resolve_moonraker_base_url_sql_only_avoids_install_state_json(self):
        from core import thumbnails

        install_state_path = os.path.join(os.getcwd(), "data", "install_state.json")
        orig_exists = thumbnails.os.path.exists

        def guarded_exists(path):
            if os.path.abspath(path) == os.path.abspath(install_state_path):
                raise AssertionError("SQL-only thumbnails should not probe install_state.json")
            return orig_exists(path)

        with patch.object(thumbnails.os.path, "exists", side_effect=guarded_exists):
            resolved = thumbnails.resolve_moonraker_base_url("SV08")

        self.assertIsNone(resolved)

    def test_maybe_run_auto_backup_sql_only_skips_file_backed_archive_runtime(self):
        from core import backup

        self._upsert_user_setting(
            "backup_settings",
            {
                "auto_backup_enabled": True,
                "auto_backup_frequency": "daily",
                "auto_backup_keep": 3,
                "last_auto_backup_ts": 0.0,
            },
        )

        with patch.object(backup, "create_backup_archive", side_effect=AssertionError("should not archive in SQL-only runtime")):
            ran, archive, error = backup.maybe_run_auto_backup()

        self.assertFalse(ran)
        self.assertIsNone(archive)
        self.assertEqual(error, "Automatic backups are disabled in SQL-only mode.")

    def test_save_printer_defaults_sql_only_rejects_missing_required_pricing_fields(self):
        import app as kcd_app
        from core import storage

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/printers",
            data={
                "action": "save_printer_defaults",
                "printer": "SV08",
                "filament_mode": "per_meter",
                "filament_rate": "0.25",
                "grams_per_meter": "3.0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Missing hourly rate.")
        self.assertEqual(storage.load_settings("ignored").get("SV08"), None)

    def test_add_filament_profile_sql_only_rejects_missing_required_pricing_fields(self):
        import app as kcd_app
        from core import profiles

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "add_filament_profile",
                "profile_name": "PLA Red",
                "material": "PLA",
                "filament_mode": "per_meter",
                "grams_per_meter": "3.0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Missing filament rate.")
        self.assertEqual(profiles.get_all_profiles(), {})

    def test_add_rate_profile_sql_only_rejects_missing_required_rate(self):
        import app as kcd_app
        from core import rates

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "add_rate_profile",
                "rate_profile_name": "Fast",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Missing hourly rate.")
        self.assertEqual(rates.list_rate_profiles(), {})

    def test_update_filament_profile_sql_only_rejects_invalid_required_pricing_fields(self):
        import app as kcd_app
        from core import profiles

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("pla-red", "PLA Red", "PLA", "per_meter", 2.5, None, 3.0, now, now),
            )
            conn.commit()

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "update_filament_profile",
                "profile_id": "pla-red",
                "profile_name": "PLA Red",
                "material": "PLA",
                "brand": "Brand",
                "color": "Red",
                "filament_mode": "per_meter",
                "filament_rate": "bad",
                "grams_per_meter": "3.5",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Invalid filament rate (must be a non-negative number).")
        profile = profiles.get_profile("pla-red")
        self.assertAlmostEqual(float(profile.get("filament_rate") or 0.0), 2.5, places=6)
        self.assertAlmostEqual(float(profile.get("grams_per_meter") or 0.0), 3.0, places=6)

    def test_update_rate_profile_sql_only_rejects_missing_required_rate(self):
        import app as kcd_app
        from core import rates

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rate-fast", "Fast", "", 12.0, now, now),
            )
            conn.commit()

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "update_rate_profile",
                "rate_profile_id": "rate-fast",
                "rate_profile_name": "Fast",
                "rate_profile_description": "Updated",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Missing hourly rate.")
        profile = rates.get_rate_profile("rate-fast")
        self.assertAlmostEqual(float(profile.get("rate_per_hour") or 0.0), 12.0, places=6)

    def test_set_active_filament_profile_sql_only_rejects_unknown_profile(self):
        import app as kcd_app
        from core import profiles

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("pla-red", "PLA Red", "PLA", "per_meter", 2.5, None, 3.0, now, now),
            )
            conn.commit()
        self._upsert_user_setting("filament_mappings", {"SV08": "pla-red"})

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "set_active_filament_profile",
                "printer": "SV08",
                "profile_id": "missing-profile",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Filament profile not found: missing-profile")
        self.assertEqual(profiles.get_printer_mapping("SV08"), "pla-red")

    def test_set_active_filament_profile_sql_only_allows_explicit_clear(self):
        import app as kcd_app
        from core import profiles

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filament_profiles
                    (profile_uid, name, material, filament_mode, filament_rate, cost_per_kg, grams_per_meter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("pla-red", "PLA Red", "PLA", "per_meter", 2.5, None, 3.0, now, now),
            )
            conn.commit()
        self._upsert_user_setting("filament_mappings", {"SV08": "pla-red"})

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "set_active_filament_profile",
                "printer": "SV08",
                "profile_id": "none",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "")
        self.assertIsNone(profiles.get_printer_mapping("SV08"))

    def test_set_active_rate_profile_sql_only_rejects_unknown_profile(self):
        import app as kcd_app
        from core import storage

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rate-fast", "Fast", "", 12.0, now, now),
            )
            conn.commit()
        self._upsert_user_setting("printer_settings", {"SV08": {"active_rate_profile_id": "rate-fast"}})

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "set_active_rate_profile",
                "printer": "SV08",
                "rate_profile_id": "missing-rate",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "Rate profile not found: missing-rate")
        self.assertEqual(storage.load_settings("ignored").get("SV08", {}).get("active_rate_profile_id"), "rate-fast")

    def test_set_active_rate_profile_sql_only_allows_explicit_clear(self):
        import app as kcd_app
        from core import storage

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hourly_rate_profiles (profile_uid, name, description, rate_per_hour, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("rate-fast", "Fast", "", 12.0, now, now),
            )
            conn.commit()
        self._upsert_user_setting("printer_settings", {"SV08": {"active_rate_profile_id": "rate-fast"}})

        client = kcd_app.app.test_client()
        response = client.post(
            "/settings/profiles",
            data={
                "action": "set_active_rate_profile",
                "printer": "SV08",
                "rate_profile_id": "none",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._redirect_error(response), "")
        self.assertIsNone(storage.load_settings("ignored").get("SV08", {}).get("active_rate_profile_id"))


if __name__ == "__main__":
    unittest.main()

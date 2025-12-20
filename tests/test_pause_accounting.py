import unittest
from unittest.mock import patch


class PauseAccountingTests(unittest.TestCase):
    def test_compute_costs_excludes_paused_time_when_include_disabled(self):
        from core import pricing as pricing_module

        def fake_get_effective_rate_per_hour(_printer_name):
            return 10.0

        def fake_get_filament_pricing(_printer_name):
            return {"filament_mode": "per_meter", "filament_rate": 0.0, "grams_per_meter": 0.0}

        with patch.object(pricing_module, "get_effective_rate_per_hour", fake_get_effective_rate_per_hour), patch.object(
            pricing_module, "_get_effective_filament_pricing", fake_get_filament_pricing
        ), patch.object(
            pricing_module,
            "load_display_settings",
            lambda *_args, **_kwargs: {"pause_include_paused_time_default": False},
        ), patch.object(
            pricing_module,
            "load_settings",
            lambda *_args, **_kwargs: {"SV08": {}},
        ):
            # duration=2h, paused=1h -> billable_seconds=1h -> billable_hours=1.0 -> time_cost=10.0
            out = pricing_module.compute_costs("SV08", 7200.0, 0.0, paused_seconds_total=3600.0)
            self.assertAlmostEqual(float(out.get("time_cost") or 0.0), 10.0, places=6)

    def test_compute_costs_override_enables_include(self):
        from core import pricing as pricing_module

        def fake_get_effective_rate_per_hour(_printer_name):
            return 10.0

        def fake_get_filament_pricing(_printer_name):
            return {"filament_mode": "per_meter", "filament_rate": 0.0, "grams_per_meter": 0.0}

        settings = {
            "SV08": {
                "pause_include_paused_time_override_enabled": True,
                "pause_include_paused_time_override_value": True,
            }
        }

        with patch.object(pricing_module, "get_effective_rate_per_hour", fake_get_effective_rate_per_hour), patch.object(
            pricing_module, "_get_effective_filament_pricing", fake_get_filament_pricing
        ), patch.object(
            pricing_module,
            "load_display_settings",
            lambda *_args, **_kwargs: {"pause_include_paused_time_default": False},
        ), patch.object(
            pricing_module,
            "load_settings",
            lambda *_args, **_kwargs: settings,
        ):
            # Include-paused override is enabled -> billable_seconds=2h -> time_cost=20.0
            out = pricing_module.compute_costs("SV08", 7200.0, 0.0, paused_seconds_total=3600.0)
            self.assertAlmostEqual(float(out.get("time_cost") or 0.0), 20.0, places=6)


if __name__ == "__main__":
    unittest.main()

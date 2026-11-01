import unittest
from unittest.mock import patch

from core import history_repo


class HistoryRepoCostFallbackTests(unittest.TestCase):
    def test_compute_job_cost_fields_populates_time_cost(self):
        row = {
            "printer": "SV08",
            "duration_seconds": 1800,
            "filament_mm": 0,
            "paused_seconds_total": 0,
            "duration_hours": None,
            "rate_per_hour": None,
            "time_cost": None,
            "material_cost": None,
            "total_cost": None,
        }
        fake = {
            "duration_hours": 0.5,
            "filament_meters": 0.0,
            "rate_per_hour": 2.0,
            "filament_mode": "per_meter",
            "filament_rate": 0.0,
            "grams_per_meter": 1.0,
            "time_cost": 2.0,
            "material_cost": 0.0,
            "total_cost": 2.0,
            "filament_profile_id": "",
            "filament_material": "",
        }
        with patch("core.history_repo.pricing.compute_costs_with_overrides", return_value=fake):
            history_repo.compute_job_cost_fields(row)

        self.assertEqual(row.get("time_cost"), 2.0)
        self.assertEqual(row.get("duration_hours"), 0.5)

    def test_explicit_zero_prices_and_signed_totals_are_historical_values(self):
        row=dict(printer='Fixture',duration_seconds=3600,filament_mm=1000,
                 rate_per_hour=0,time_cost=0,material_cost=0,total_cost=-5)
        with patch('core.history_repo.pricing.compute_costs_with_overrides',return_value=dict(rate_per_hour=10,time_cost=10,material_cost=2,total_cost=12)):
            history_repo.compute_job_cost_fields(row)
        self.assertEqual((row['rate_per_hour'],row['time_cost'],row['material_cost'],row['total_cost']),(0,0,0,-5))


if __name__ == "__main__":
    unittest.main()

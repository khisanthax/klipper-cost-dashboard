import os
import tempfile
import unittest
from unittest import mock


os.environ.setdefault("KCD_SQL_ONLY_FAIL_FAST", "0")

from core import db as db_module
from core import projects
from core.numeric import NumericValidationError


class ProjectCostComponentTests(unittest.TestCase):
    def setUp(self):
        self.project = projects.Project(
            id="p1",
            name="Components",
            hourly_rate_override=10.0,
            filament_cost_per_kg_override=20.0,
        )

    def assertInvariant(self, components):
        self.assertAlmostEqual(
            components["time_cost"]
            + components["material_cost"]
            + components["adjustment"],
            components["total_cost"],
            places=12,
        )

    def manual(self, **overrides):
        values = {
            "manual_job_id": "m1",
            "project_id": "p1",
            "title": "Manual work",
            "hours": 2.0,
            "filament_g": 500.0,
        }
        values.update(overrides)
        return projects.ManualJob(**values)

    def plan(self, **overrides):
        values = {
            "plan_id": "pl1",
            "project_id": "p1",
            "filename": "part.gcode",
            "created_at": "2026-08-17T00:00:00Z",
            "est_time_s": 1800,
            "est_filament_g": 500.0,
        }
        values.update(overrides)
        return projects.PlannedItem(**values)

    def test_tracked_components_preserve_signed_residual_and_incomplete_rows(self):
        cases = (
            ({"time_cost": 4.0, "material_cost": 6.0, "total_cost": 10.0}, 0.0),
            ({"time_cost": 4.0, "material_cost": 6.0, "total_cost": 12.0}, 2.0),
            ({"time_cost": 4.0, "material_cost": 6.0, "total_cost": 8.0}, -2.0),
            ({"total_cost": 7.5}, 7.5),
        )
        for row, expected_adjustment in cases:
            with self.subTest(row=row):
                components = projects.compute_tracked_job_cost_components(row)
                self.assertEqual(components["adjustment"], expected_adjustment)
                self.assertEqual(components["total_cost"], row["total_cost"])
                self.assertInvariant(components)

    def test_manual_components_preserve_pricing_labor_only_and_zero_rates(self):
        normal = projects.compute_manual_job_cost_components(self.manual(), project=self.project)
        self.assertEqual(normal, {
            "time_cost": 20.0,
            "material_cost": 10.0,
            "adjustment": 0.0,
            "total_cost": 30.0,
        })

        labor_only = projects.compute_manual_job_cost_components(
            self.manual(),
            project=projects.Project(
                id="p2",
                name="Labor",
                hourly_rate_override=10.0,
                filament_cost_per_kg_override=20.0,
                labor_only=True,
            ),
        )
        self.assertEqual(labor_only["material_cost"], 0.0)
        self.assertEqual(labor_only["total_cost"], 20.0)
        self.assertInvariant(labor_only)

        zero = projects.compute_manual_job_cost_components(
            self.manual(), hourly_rate=0.0, filament_cost_per_kg=0.0, labor_only=False
        )
        self.assertEqual(zero["total_cost"], 0.0)
        self.assertInvariant(zero)

    def test_manual_override_is_exact_total_with_signed_adjustment(self):
        above = projects.compute_manual_job_cost_components(
            self.manual(cost_override=35.0), project=self.project
        )
        below = projects.compute_manual_job_cost_components(
            self.manual(cost_override=25.0), project=self.project
        )
        self.assertEqual(above["adjustment"], 5.0)
        self.assertEqual(below["adjustment"], -5.0)
        self.assertInvariant(above)
        self.assertInvariant(below)

    def test_planned_components_preserve_minimum_and_signed_override(self):
        normal = projects.compute_planned_item_cost_components(self.plan(), self.project)
        above = projects.compute_planned_item_cost_components(
            self.plan(est_cost=25.0, est_cost_is_override=True), self.project
        )
        below = projects.compute_planned_item_cost_components(
            self.plan(est_cost=15.0, est_cost_is_override=True), self.project
        )
        self.assertEqual(normal["time_cost"], 10.0)
        self.assertEqual(normal["material_cost"], 10.0)
        self.assertEqual(normal["total_cost"], 20.0)
        self.assertEqual(above["adjustment"], 5.0)
        self.assertEqual(below["adjustment"], -5.0)
        for components in (normal, above, below):
            self.assertInvariant(components)

    def test_actual_and_projected_aggregation_keep_membership_and_invariant(self):
        tracked = [{
            "duration_hours": 1.25,
            "filament_meters": 3.5,
            "time_cost": 4.0,
            "material_cost": 6.0,
            "total_cost": 9.0,
        }]
        actual = projects.compute_project_totals(
            tracked,
            manual_jobs=[self.manual(cost_override=35.0)],
            project=self.project,
        )
        self.assertEqual(actual["prints"], 2.0)
        self.assertEqual(actual["time_cost"], 24.0)
        self.assertEqual(actual["material_cost"], 16.0)
        self.assertEqual(actual["adjustment"], 4.0)
        self.assertEqual(actual["total_cost"], 44.0)
        self.assertEqual(actual["cost"], actual["total_cost"])
        self.assertInvariant(actual)

        active = self.plan(est_cost=25.0, est_cost_is_override=True)
        fulfilled = self.plan(plan_id="pl2", status="fulfilled", est_cost=100.0, est_cost_is_override=True)
        projected = projects.compute_project_projection([active, fulfilled], self.project)
        self.assertEqual(projected["count"], 1.0)
        self.assertEqual(projected["hours"], 0.5)
        self.assertEqual(projected["filament_g"], 500.0)
        self.assertEqual(projected["total_cost"], 25.0)
        self.assertEqual(projected["cost"], projected["total_cost"])
        self.assertInvariant(projected)

    def test_aggregation_does_not_round_components_early(self):
        rows = [
            {"time_cost": 0.0049, "material_cost": 0.0049, "total_cost": 0.0098},
            {"time_cost": 0.0049, "material_cost": 0.0049, "total_cost": 0.0098},
        ]
        totals = projects.compute_project_totals(rows)
        self.assertAlmostEqual(totals["time_cost"], 0.0098, places=12)
        self.assertAlmostEqual(totals["material_cost"], 0.0098, places=12)
        self.assertAlmostEqual(totals["total_cost"], 0.0196, places=12)
        self.assertEqual(totals["adjustment"], 0.0)
        self.assertInvariant(totals)

    def test_equivalent_inputs_are_backend_independent(self):
        row = {"time_cost": 1.25, "material_cost": 2.5, "total_cost": 3.0}
        results = []
        for backend in ("csv", "dual", "sql"):
            with mock.patch.dict(os.environ, {"KCD_STORAGE_BACKEND": backend}):
                results.append(projects.compute_project_totals([dict(row)]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertInvariant(results[0])

    def test_planned_inputs_reject_nonfinite_and_negative_values(self):
        with mock.patch.object(projects, "load_projects", return_value={"p1": self.project}):
            for field, value in (
                ("est_time_s", float("nan")),
                ("est_time_s", float("inf")),
                ("est_time_s", -1),
                ("est_filament_g", float("-inf")),
                ("est_filament_g", -1),
                ("est_cost_override", float("nan")),
                ("est_cost_override", -1),
            ):
                kwargs = {
                    "project_id": "p1",
                    "filename": "part.gcode",
                    "est_time_s": 60,
                    "est_filament_g": 1.0,
                }
                kwargs[field] = value
                with self.subTest(field=field, value=value):
                    with self.assertRaises(NumericValidationError):
                        projects.create_plan_item(**kwargs)

    def test_zero_planned_override_remains_valid(self):
        with (
            mock.patch.object(projects, "load_projects", return_value={"p1": self.project}),
            mock.patch.object(projects, "load_plans", return_value={}),
            mock.patch.object(projects, "_save_plans"),
        ):
            item = projects.create_plan_item(
                project_id="p1",
                filename="part.gcode",
                est_time_s=60,
                est_filament_g=0.0,
                est_cost_override=0.0,
            )
        self.assertTrue(item.est_cost_is_override)
        self.assertEqual(item.est_cost, 0.0)
        components = projects.compute_planned_item_cost_components(item, self.project)
        self.assertLess(components["adjustment"], 0.0)
        self.assertInvariant(components)

    def test_component_reporting_requires_no_schema_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_file = os.path.join(temp_dir, "kcd.db")
            with mock.patch.object(db_module, "_db_path", return_value=db_file):
                with db_module.connect_db() as conn:
                    db_module.apply_migrations(conn)
                    manual_columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(project_manual_jobs)").fetchall()
                    }
                    planned_columns = {
                        row[1] for row in conn.execute("PRAGMA table_info(project_plans)").fetchall()
                    }
        for columns in (manual_columns, planned_columns):
            self.assertNotIn("time_cost", columns)
            self.assertNotIn("material_cost", columns)
            self.assertNotIn("adjustment", columns)


class ProjectCostComponentViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module

        cls.app_module = app_module
        cls.flask_app = app_module.app
        cls.flask_app.config.update(TESTING=True)

    def render_projects(self, *, show_costs=True, tracked_columns=None):
        tracked_columns = tracked_columns or list(self.app_module.PROJECTS_TRACKED_COLUMNS)
        project = {
            "id": "p1",
            "name": "Components",
            "notes": "",
            "totals": {
                "prints": 2.0,
                "hours": 3.0,
                "meters": 4.0,
                "filament_g": 500.0,
                "time_cost": 24.0,
                "material_cost": 16.0,
                "adjustment": -1.0,
                "total_cost": 39.0,
            },
            "jobs": [{
                "job_uid": "j1",
                "date": "2026-08-17",
                "printer": "Mercury",
                "filename": "tracked.gcode",
                "status": "completed",
                "duration_hours": 1.0,
                "filament_meters": 2.0,
                "time_cost": 10.0,
                "material_cost": 5.0,
                "adjustment": -2.5,
                "total_cost": 12.5,
            }],
            "manual_jobs": [{
                "manual_job_id": "m1",
                "title": "Manual",
                "hours": 2.0,
                "filament_g": 500.0,
                "time_cost": 20.0,
                "material_cost": 10.0,
                "adjustment": 5.0,
                "total_cost": 35.0,
                "computed_cost": 35.0,
                "cost_override": 35.0,
                "created_at": "2026-08-17",
                "notes": "",
            }],
            "planned_items": [{
                "plan_id": "pl1",
                "filename": "planned.gcode",
                "created_at": "2026-08-17",
                "est_time_s": 1800,
                "est_filament_g": 500.0,
                "est_cost": 15.0,
                "est_cost_is_override": True,
                "estimated_time_cost": 10.0,
                "estimated_material_cost": 10.0,
                "estimated_adjustment": -5.0,
                "estimated_total_cost": 15.0,
                "status": "active",
                "source": "test",
                "notes": "",
                "converted_to_manual_job_id": None,
            }],
            "projected": {
                "time_cost": 10.0,
                "material_cost": 10.0,
                "adjustment": -5.0,
                "total_cost": 15.0,
            },
            "actual_tracked_cost": 12.5,
            "actual_manual_cost": 35.0,
        }
        context = {
            "projects": [project],
            "projects_by_id": {"p1": project},
            "display_settings": {"projects_show_cost_totals": show_costs},
            "projects_project_jobs_visible_cols": tracked_columns,
            "projects_manual_jobs_visible_cols": list(self.app_module.PROJECTS_MANUAL_COLUMNS),
            "projects_planned_items_visible_cols": list(self.app_module.PROJECTS_PLANNED_COLUMNS),
            "projects_unassigned_visible_cols": list(self.app_module.PROJECTS_UNASSIGNED_COLUMNS),
            "unassigned_jobs": [],
            "unassigned_jobs_page": [],
            "unassigned_pager": {},
            "edit_project": None,
            "edit_manual_job_id": None,
        }
        with self.flask_app.test_request_context("/projects"):
            return self.flask_app.jinja_env.get_template("projects.html").render(**context)

    def test_template_renders_actual_estimated_and_signed_components(self):
        html = self.render_projects()
        self.assertIn("Time Cost", html)
        self.assertIn("Material Cost", html)
        self.assertIn("Adjustment", html)
        self.assertIn("Est. Time Cost", html)
        self.assertIn("Est. Material Cost", html)
        self.assertIn("Est. Adjustment", html)
        self.assertIn("Actual (tracked + manual)", html)
        self.assertIn("Projected (active plans)", html)
        self.assertIn("-$2.50", html)
        self.assertIn("-$5.00", html)

    def test_cost_visibility_hides_every_monetary_component(self):
        html = self.render_projects(show_costs=False)
        for label in (
            "Time Cost",
            "Material Cost",
            "Adjustment",
            "Total Cost",
            "Est. Time Cost",
            "Est. Material Cost",
            "Est. Adjustment",
            "Est. Total Cost",
        ):
            self.assertNotIn(label, html)
        self.assertNotIn("-$2.50", html)
        self.assertNotIn("-$5.00", html)

    def test_legacy_saved_cost_column_maps_to_total_without_enabling_new_components(self):
        settings = {
            "tables": {
                "projects_project_jobs": {
                    "visible_columns": ["date", "filename", "cost"],
                }
            }
        }
        selected = self.app_module._get_projects_visible_columns(
            settings,
            "projects_project_jobs",
            self.app_module.PROJECTS_TRACKED_COLUMNS,
        )
        self.assertEqual(selected, ["date", "filename", "total_cost"])
        self.assertNotIn("time_cost", selected)
        self.assertNotIn("material_cost", selected)
        self.assertNotIn("adjustment", selected)


if __name__ == "__main__":
    unittest.main()

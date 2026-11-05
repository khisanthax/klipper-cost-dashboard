"""Versioned SQL project export that preserves recorded and projected values."""

import json


def project_document(conn, project_uid):
    project = conn.execute(
        """SELECT id, COALESCE(NULLIF(project_uid,''), CAST(id AS TEXT)) AS uid, name, notes, status,
                  hourly_rate_override, filament_cost_per_kg_override, labor_only,
                  filament_price_json, created_at, updated_at
           FROM projects WHERE COALESCE(NULLIF(project_uid,''), CAST(id AS TEXT))=?
           ORDER BY CASE WHEN project_uid=? THEN 0 ELSE 1 END LIMIT 1""", (project_uid, project_uid),
    ).fetchone()
    if project is None:
        return None
    details = dict(project)
    project_id = details.pop("id")
    details["filament_price"] = json.loads(details.pop("filament_price_json") or "null")
    jobs = conn.execute(
        """SELECT j.job_uid, p.name AS printer, j.filename, j.status, j.started_at, j.ended_at,
                  j.duration_seconds, j.filament_mm, j.filament_grams, j.filament_weight_kind,
                  j.billable_hours, j.rate_per_hour, j.filament_rate, j.filament_mode,
                  j.grams_per_meter, j.time_cost, j.material_cost, j.total_cost,
                  j.filament_profile_id, j.hourly_rate_profile_id, j.filament_material,
                  j.filament_density, j.filament_diameter, j.paused_seconds_total,
                  j.override_rate_per_hour, j.override_material_cost, j.override_total_cost,
                  j.failure_reason, j.import_source, j.import_id, j.job_outcome,
                  j.duration_seconds_raw, j.filament_mm_raw
           FROM project_assignments a JOIN jobs j USING (job_uid) JOIN printers p ON p.id=j.printer_id
           WHERE a.project_id=? ORDER BY j.ended_at, j.job_uid""", (project_id,),
    )
    manual = conn.execute(
        """SELECT manual_job_id, title, hours, filament_g, cost_override, notes, created_at, updated_at
           FROM manual_jobs WHERE project_id=? ORDER BY created_at, manual_job_id""", (project_id,),
    )
    planned = conn.execute(
        """SELECT plan_id, filename, est_time_s, est_filament_g, est_cost, est_cost_is_override,
                  status, source, notes, converted_to_manual_job_id, created_at, updated_at
           FROM planned_items WHERE project_id=? ORDER BY created_at, plan_id""", (project_id,),
    )
    snapshots = conn.execute(
        """SELECT s.job_uid, s.calculation_json, s.applied_at
           FROM project_job_pricing s JOIN project_assignments a USING (project_id, job_uid)
           WHERE s.project_id=? ORDER BY s.job_uid""", (project_id,),
    )
    original_prices = conn.execute(
        """SELECT o.job_uid,o.original_json,o.saved_at
           FROM job_cost_originals o JOIN project_assignments a USING (job_uid)
           WHERE a.project_id=? ORDER BY o.job_uid""", (project_id,),
    )
    recorded_jobs = [dict(row) for row in jobs]
    price_fields = (
        "time_cost", "material_cost", "total_cost", "rate_per_hour", "filament_mode",
        "filament_rate", "grams_per_meter", "filament_profile_id", "hourly_rate_profile_id",
        "override_rate_per_hour", "override_material_cost", "override_total_cost",
        "filament_grams", "filament_weight_kind", "filament_density", "filament_diameter",
        "billable_hours", "filament_material",
    )
    return {
        "schema_version": 1,
        "units": {"duration_seconds": "s", "hours": "h", "filament_mm": "mm", "filament_g": "g",
                  "filament_grams": "g", "rate_per_hour": "currency/h",
                  "filament_rate": "unit from filament_mode", "grams_per_meter": "g/m",
                  "costs": "installation currency"},
        "project": details, "recorded_jobs": recorded_jobs,
        "manual_work": [dict(row) for row in manual], "planned_items": [dict(row) for row in planned],
        "project_price_snapshots": [
            {"job_uid": row["job_uid"], "applied_at": row["applied_at"], "calculation": json.loads(row["calculation_json"])}
            for row in snapshots
        ],
        "original_prices": [
            {"job_uid": row["job_uid"], "saved_at": row["saved_at"],
             "pricing": {key: value for key, value in json.loads(row["original_json"]).items()
                         if key in price_fields}}
            for row in original_prices
        ],
    }

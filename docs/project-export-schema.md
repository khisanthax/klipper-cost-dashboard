# Project JSON schema version 1

On a SQL-only installation, **Download project JSON** exports a single project in
one database read transaction. The response carries attachment and no-store
headers. Missing or deleted projects return 404.

The versioned top-level fields are `schema_version`, `units`, `project`,
`recorded_jobs`, `manual_work`, `planned_items`, `project_price_snapshots` and
`original_prices`. An empty collection is an array. An unknown value is null.
The schema uses the installation's configured monetary unit.

```json
{
  "schema_version": 1,
  "units": {
    "duration_seconds": "s",
    "hours": "h",
    "filament_mm": "mm",
    "filament_g": "g",
    "filament_grams": "g",
    "rate_per_hour": "currency/h",
    "filament_rate": "unit from filament_mode",
    "grams_per_meter": "g/m",
    "costs": "installation currency"
  },
  "project": {
    "uid": "example-project",
    "name": "Example",
    "notes": null,
    "hourly_rate_override": 0,
    "filament_price": null
  },
  "recorded_jobs": [
    {
      "job_uid": "example-job",
      "printer": "Example printer",
      "status": "canceled",
      "filament_mm": 1250.25,
      "filament_grams": null,
      "time_cost": 0,
      "material_cost": null,
      "total_cost": null,
      "override_total_cost": null
    }
  ],
  "manual_work": [],
  "planned_items": [],
  "project_price_snapshots": [],
  "original_prices": []
}
```

This shortened example omits several fields for readability. Each recorded job
also includes its filename, timestamps, raw measurements, pricing units and
profile identifiers, override fields and outcome evidence. Manual entries retain
their hours, gram weight and explicit cost override. Planned entries retain their
estimate, override flag, status, source and notes. Project-adjusted calculations
stay in their own snapshot array. First original price snapshots are restricted
to known pricing and conversion fields for the assigned job.

Numerical fields use JSON numbers at the precision saved in SQLite. Export adds
no rounding, currency conversion or length/weight conversion. Consumers that need
decimal arithmetic should parse numbers as decimal values and honor the original
pricing unit. SQLite REAL values have their existing floating-point precision.
Zero and signed values retain their meaning.

The export excludes other projects, current Master credentials, endpoints and
host configuration. Import and data restoration use separately validated
workflows. Retain coordinated SQL/object backups for disaster recovery.

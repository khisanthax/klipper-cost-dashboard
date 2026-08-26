# Changelog

## v0.5.1 - 2026-08-23

### Fixed
- Fixed direct Docker invocation of `python tools/kcd_backup.py` without requiring a `PYTHONPATH` override.
- Hardened backup archives to owner-only `0600` permissions because they may contain credentials.

## v0.5.0 - 2026-08-17

### Added
- Added Projects cost-component reporting for tracked jobs, manual work, planned items, and project totals using `Time Cost + Material Cost + Adjustment = Total Cost`.
- Added signed adjustment handling for stored and explicit total overrides, configurable component columns, and finite/nonnegative planned-item input validation.

## v0.4.0 - 2026-08-16

### Added
- Added reusable SQL-only readiness validation shared by startup, `/health`, and `python -m kcd db readiness`.
- Added default fail-fast startup for strict SQL-only mode, with `KCD_SQL_ONLY_FAIL_FAST=0` as a diagnostic escape hatch.
- Added representative pre-import and runtime filesystem monitoring with explicit credential, cache, export, backup, and temporary-file exceptions.
- Added `KCD_API_KEY` as an environment override for the `secret.json` credential fallback.
- Added SQL-only printer rename, merge, retirement, and linked-state regression coverage.
- Added explicit printer reactivation through deliberate installer re-registration while preserving historical identity.
- Added filament usage, time cost, material cost, and total cost visibility to Recalculate jobs and previews.
- Added release CI covering the full test suite, SQL-only validation, production Docker build, and in-container tooling smoke tests.

### Changed
- Made SQL-backed configuration canonical in SQL-only mode, including pricing, pause policy, display settings, active profile mappings, projects, and system events.
- Defined CSV and dual mode as supported compatibility runtime modes for this release line while keeping SQL-only strict/canonical.
- Changed Docker healthchecks to use `/health` readiness rather than the dashboard route.
- Changed SQL-capable installer services to use consistent `dual` writes with automatic SQL Reports reads.
- Changed the Docker image to install pinned requirements and include the KCD CLI and operational tools.
- Retired printers now remain available to historical jobs/events but are excluded from readiness, active discovery, and new incoming jobs.
- Recalculate is explicitly pricing-only; Full Recompute is deferred until its semantics are designed.
- SQL-only Recalculate audit activity is persisted through SQL system events instead of JSONL.

### Fixed
- Surfaced canonical SQL read and write failures instead of presenting false empty state or successful persistence.
- Kept Recalculate preview and execution consistent for pause-accounting inputs.
- Isolated strict SQL-only lifecycle and representative routes from compatibility CSV/JSON and installer-state paths.
- Corrected job-cancel test isolation so tests target the actual storage backend path.
- Enforced fail-closed API-key authentication across every printer-client mutation endpoint.
- Created SQLite-consistent backup snapshots that restore without live WAL/SHM files; SQL-only automatic scheduling is now visibly unavailable rather than silently ignored.
- Surfaced SQL mutation failures without false success or audit events and preserved transactional rollback.
- Rejected non-finite, negative, and calculation-invalid pricing, measurements, timestamps, and recalculation overrides while preserving valid zero-cost pricing.

## v0.3.0 - 2026-01-20

### Added
- Added the SQLite foundation, schema migrations, legacy CSV/JSON import, dual writes, parity verification, and backfill tooling.
- Added SQL-backed Print History and Reports repositories, DB-backed report caching, SQL CSV export, and history/report parity tools.
- Added SQL-backed Projects, assignments, manual jobs, and planned items plus explicit legacy Projects import tooling.
- Added installer awareness for database initialization/import and stable external printer identities.
- Added SQL persistence for printer settings, filament and hourly-rate profiles, backup settings, system events, thumbnail references, and Moonraker URLs.
- Added SQL-only validation helpers, runtime file guards, Moonraker history import, thumbnail diagnostics, and printer diagnostics.

### Changed
- Forced History and Reports to SQL when strict SQL-only mode is selected.
- Kept active live-job state in memory and thumbnail images in an explicit cache during SQL-only runtime.
- Expanded explicit SQL export and Moonraker backfill workflows for installations where SQL contains more history than legacy CSV.

### Fixed
- Corrected duration/time-cost persistence and completed-job finalization using Moonraker history.
- Hardened report-cache commits, timestamp parity, printer lookup, profile mappings, Moonraker URL handling, and external-ID uniqueness migration.
- Prevented SQL-only Projects, Recalculate, profile, and storage paths from falling back to legacy runtime files.

## v0.2.0
- Added System Events audit trail with filters and expandable details for warnings, deletions, and failures.
- Added Moonraker history import with inferred outcomes and effective durations for cancelled jobs.
- Added pause tracking, counts, and per-printer pause accounting settings.
- Improved Print History pagination, filters, column visibility, and CSV schema repair.
- Expanded Projects with manual jobs, planned items, recalculation tools, and totals.
- Improved installer master/client flows, cancel scripts, and Moonraker URL detection.

## v0.1.0
- Initial public release with CSV-based print cost tracking.

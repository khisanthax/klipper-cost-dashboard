# Changelog

## v0.10.0 - 2026-11-05

- Add physical spool inventory with exact milligram accounting, explicit verified consumption, idempotent replay, linked corrections, manual adjustment reasons, archival and recovery. Estimated consumption does not silently deduct physical stock or rewrite historical costs.
- Add monthly saved outcomes, measurements and cost coverage, local-time/printer filters and prior-month comparisons. Preserve unknown/zero/signed values and invalidate SQL reports after relevant content changes.
- Give explicit Moonraker terminal outcomes precedence during new imports, and prevent permanent-thumbnail deletion racing new attachments.
- Additive 0010/0011 inventory migrations retain prior business rows. [Verified production deployment and v0.9.0 rollback evidence](docs/v0.10.0-deployment-2026-11-05.md).

## v0.9.0 - 2026-11-05

- Add Personal, Advanced and Business presentation modes; existing installations retain Advanced and switching preserves business data.
- Add SQL project-backed Orders with Business-mode guards, lifecycle tracking, optional customer/scheduling data, distinct cost/quote/price/revenue/profit fields and unknown-value handling without repricing historical prints.
- Apply pending SQL migration DDL and its ledger entry atomically; preserve additive 0009 schema and recovery behavior under failures/concurrency.
- [Verified production deployment and rollback evidence](docs/v0.9.0-deployment-2026-11-05.md): immutable image, fresh restored backup, unchanged historical data, Advanced default and direct Orders boundary.

## v0.8.0 - 2026-11-05

- Add individual registered-printer pages with verified telemetry, missing-value handling, recorded-cost history, date comparisons and stable pagination.
- Add spool-price/explicit-unit pre-print estimates and signed, previewed promotion to planned items without changing history; preserve rounded reviewed totals and raw assumptions.
- Make SQL plan/manual create/edit/delete/conversion targeted and transactional so overlapping work cannot erase unrelated data; preserve unknown weights and canonical project identity.
- Add consistent, versioned SQL project JSON export with separate recorded/original/adjusted/manual/planned values, explicit units and private project isolation.
- [Verified production deployment/rollback evidence](docs/v0.8.0-deployment-2026-11-05.md), including fresh restored-data checks and actual read-only printer/estimate/export/installer acceptance.

## v0.7.4 - 2026-11-05

- Correct Klipper argument delivery and targeted managed cancel payloads, preserving custom macros and legacy client compatibility.
- Preserve measured cancel consumption, unknown/unpriced history, per-attempt identity and atomic SQL history/live/audit rollback; reject delayed mismatched attempts.
- Show the two previously recovered SV07 rows as Canceled and Unpriced without replaying recovery or recalculating historical prices.
- Adopt the owner-authorized solo-maintainer policy: required PRs, strict Release Validation/Python checks and resolved threads with zero human approvals; no administrator bypass.
- Gate upgrades/rollbacks on nonempty projects/profiles/assignments/manual/planned/original-pricing data, exact schema/object preservation and optional new-feature records.
- [Verified production deployment and rollback evidence](docs/v0.7.4-deployment-2026-11-05.md).

## v0.7.3 - 2026-11-03

- Add bounded current Moonraker observations, loaded client identity checks and stale-event warnings without modifying retained jobs; update live estimates without reloading forms.
- Protect assigned pricing profiles from deletion, preserve explicit zero overrides, expose project profile snapshots and unknown weights, and remove repeated project catalog reads.
- Repair History session selection, bulk error messages, independent printer Today totals, report charts and quoted confirmation dialogs.
- Bound missing-thumbnail recovery and endpoint retries while preserving permanent archives and metadata-only status checks.
- Improve shared labels, keyboard navigation, contrast and responsive layouts. Gate changes on 66 page/layout checks, axe, financial/project workflows and an actual v0.7.2 Docker upgrade.
- Document the candidate's acceptance findings, pricing semantics, evidence and explicit production/release limitations.

## v0.7.2 - 2026-11-01

- Gate installer releases on actual terminal menus, disposable OpenSSH deployment, native systemd installation/upgrade/uninstall/reinstall, Docker authority detection and real Chromium workflows in SQL, CSV and dual modes. Retain screenshots, traces and redacted transcripts.
- Make discovery read-only; preserve selected update identities; repair cancel navigation, unchanged-file previews, custom script paths and private script ownership. Reject managed symlinks escaping the selected config directory.
- Verify source service health before success, persist the intended private credential and handle fresh sudo-created runtime ownership. Restart forced upgrades; make uninstall remove only its own service while retaining recoverable data and source.
- Persist legacy client metadata without duplicating relocated identities; bind client credential reuse to the selected Master and keep SSH staging credentials in memory. Fresh legacy installs no longer register example printers.
- Associate printer Settings labels with their controls and wrap narrow headers. Document the tested acceptance matrix and read-only production reference; MercuryTwo uses Moonraker port 7129.

## v0.7.1 - 2026-11-01

- Allocate fractional cents across Recalculate Center preview rows so displayed components and totals agree; retain full SQL calculation precision and suppress negative zero adjustments.
- Drain Waitress on SIGTERM, including Docker PID 1, and verify active SQL jobs survive a server restart.
- Identify selected filament profiles as the conversion source. Prevent same-name profile creation or rename from replacing another profile identity.

## v0.7.0 - 2026-11-01

- Fix Recalculate Center submitting stale profile settings; require reviewed SQL previews, preserve original costs, validate actual updates and commit before/after audit evidence atomically. Preserve legitimate historical zero costs.
- Unify explicit filament units, spool price/net weight and density/diameter or verified g/m conversion across settings, profiles, projects and recalculation. Add project-only pricing snapshots and reconciled signed cost components.
- Archive print thumbnails outside SQLite with stable job references, start/completion/cancel capture, conservative cache recovery, bounded backfill, explicit pruning and coordinated backup coverage.
- Make History selection visible, accessible and sticky while scrolling; protect numeric job UIDs and verify exact bulk-action scope. Keep remote thumbnail retrieval out of HTML rendering.
- Vendor integrity-checked Bootstrap 5.3.8 for offline deployments and reject browser cross-origin mutations.
- Add Chromium regression suites for responsive/zoom selection, 15-job $0/hour/$15/kg recalculation, profile spool inputs and thumbnail survival after source deletion, printer relocation and restart. Gate releases on these checks.

## v0.6.2 - 2026-11-01

- Prepare and verify bash interpreter commands before Moonraker credential propagation, because uploads replace executable permissions. Add a regression smoke test for that behavior.

## v0.6.1 - 2026-11-01

- Resolve effective macro definitions in Klipper's linear include/override order, retaining shadowed definitions and shared symlink semantics.

## v0.6.0 - 2026-11-01

- Unified client installation/repair around authenticated Master registration, transactional SQL identity/pricing initialization and recovered installation metadata.
- Added bounded Klipper instance discovery, Moonraker identity deduplication and config-root validation from the Master network.
- Added active-include macro inspection, dry-run diffs and targeted, idempotent integration.
- Preserved live SQL jobs across restarts; retained live state on failed cancellation; fixed SQL duplicate cancellation and completion retries.
- Removed the tracked API credential, masked prompts, secured generated scripts/backups and added recoverable coordinated key rotation.
- Replaced Flask's development server with Waitress; pinned/audited dependencies and gated multi-architecture image publication on release validation.
- Added production audit, recovery/rollback instructions and regression coverage.

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

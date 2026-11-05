# Klipper Cost Dashboard - Unified Roadmap

## Purpose

The [completion execution plan](completion-program.md) records the November 5
inventory, branch dependencies, release sequence and externally imposed gates.
Personal mode includes Projects under the current owner directive. Full
Recompute remains deferred.

This document is the single source of truth for Klipper Cost Dashboard roadmap planning. It captures:

- project history
- current state
- next planned work

It is derived from the audited repository state, not from older handoff notes or memory. If other roadmap notes disagree with this document, this document should win unless a newer repo-backed audit proves otherwise.

## Phase 1 - Core System

- status: complete
- summary:
  - KCD started as a CSV/JSON-backed Flask application for Klipper print cost tracking.
  - It established the ingestion routes, cost calculation model, print history, and the first dashboard/settings UI.
  - The original architecture favored transparency and simple local files over relational structure.
- key outcome:
  - A working end-to-end print cost dashboard existed before any SQL work began.

## Phase 2 - Multi-Printer + Data Normalization

- status: complete
- summary:
  - The project expanded from single-flow CSV logging into a multi-printer system with more durable identity and normalization concerns.
  - Stable `job_uid` support was added.
  - Projects and assignments were migrated off fragile legacy row-key assumptions.
  - Recalculation and printer identity tooling were introduced on top of this normalized layer.
- key outcome:
  - The app gained stable row identity and a stronger data model, which later enabled SQL import, parity, and assignment migration.

## Phase 3 - SQL Foundation / Migration

- status: complete
- summary:
  - SQLite was introduced as an additive backend, not a replacement.
  - The project added schema migrations, DB initialization, CSV import, backfill, verification, and dual-write support.
  - SQL tables covered more than history alone: printers, profiles, projects, jobs, user settings, and later follow-on tables.
- key outcome:
  - SQL became a real backend with migration and parity tooling, while CSV remained available for compatibility and validation.

## Phase 4 - SQL UI Parity

- status: effectively complete
- summary:
  - History and Reports gained SQL-backed read repositories.
  - Projects also gained substantial SQL backing, including assignments, manual jobs, and planned items.
  - SQL-only overrides were later added so history and reports no longer fall back to CSV in SQL-only mode.
- key outcome:
  - Major read-heavy UI surfaces can operate from SQL rather than CSV.
- important notes:
  - This phase is effectively complete for history and reports.
  - CSV and JSON behavior remains deliberately available in compatibility modes, while strict SQL-only routes use SQL-backed state.

## Phase 5 - Installer SQL Awareness

- status: complete
- summary:
  - The installer became aware of CSV-only, SQL-capable, and dual/compat installs.
  - It can initialize the DB, apply migrations, import CSV into SQL, and sync printer mappings.
  - SQL-aware install flows were added without dropping backward compatibility.
- key outcome:
  - Existing installs can move toward SQL without a manual migration-only workflow.

## Phase 6 - SQL-Only Hardening and Canonicalization

This phase shipped in v0.4.0 on 2026-08-17 and is complete for the current release contract.

### 6.1 Guardrails

- status: complete
- what is already done:
  - `KCD_STORAGE_BACKEND=sql` exists.
  - `SqlOnlyViolationError` exists.
  - file-backed read/write guard helpers exist and are used broadly.
  - SQL-only validation tooling and targeted guard tests exist.
- what remains:
  - broaden validation coverage where useful, but the core guardrail mechanism is already in place.
- why it matters:
  - Without loud guardrails, SQL-only regressions silently reintroduce file-backed behavior.

### 6.2 Remove Implicit CSV Runtime Behavior

- status: complete
- what is already done:
  - SQL-only blocks many file-backed reads and writes.
  - auto-create/runtime bootstrap behavior is blocked in SQL-only.
  - history and reports are forced to SQL in SQL-only mode.
  - runtime file-read migration behavior has been removed from `core/storage.py` for `load_settings()` and `load_display_settings()`.
  - printer rename, merge, and delete now use transactional SQL-only lifecycle paths without compatibility file or installer-state access.
  - hidden SQL printer rows now represent retired historical identities and are excluded from readiness, active discovery, and incoming logging.
  - startup and representative runtime validation observes direct file opens and blocks known legacy business-state files while allowing deliberate credential/cache/export/backup exceptions.
- future hardening:
  - representative isolation coverage can be broadened as new runtime paths are added.
- why it matters:
  - SQL-only is not fully pure until runtime behavior is independent of legacy files.

### 6.3 Startup Verification

- status: complete
- what is already done:
  - `/health` has a SQL-safe path.
  - `/health` now reports SQL-only readiness through a reusable validator instead of DB connectivity alone.
  - SQL-only now fails fast at startup by default through a small hook that uses the readiness validator.
  - readiness verifies minimum load-bearing SQL-only runtime state: DB connectivity, migrations/schema version, required SQL tables, persisted pause billing default, and configured-printer pricing/profile calculation readiness.
  - Moonraker diagnostics exist.
  - a lightweight SQL-only validation helper exists.
  - `python -m kcd db readiness` exposes the same readiness validator as a preflight command with actionable output and process exit status.
  - the Docker healthcheck uses `/health` rather than the dashboard route.
- what remains:
  - future hardening can broaden certification coverage where useful, but the practical startup readiness contract is now in place.
- why it matters:
  - SQL-only needs an explicit verification story, not just best-effort route hardening.

### 6.4 DB as Canonical Config

- status: complete
- what is already done:
  - Moonraker URL can be edited in the UI and stored in the DB.
  - SQL-only printer settings, pause accounting policy, display state, and filament mappings use `user_settings`.
  - filament and hourly rate profiles use their SQL tables, and active profile references are validated before use.
  - backup settings can persist in SQL-only.
  - SQL-only pricing now resolves from DB-backed settings and profile state instead of falling back to CSV or implicit default pricing during normal runtime.
  - required pricing and pause-accounting state fail loudly through runtime validation and startup readiness, while presentation and disabled-feature defaults remain intentionally SQL-safe.
- what remains:
  - no remaining default, fallback, or persistence-error ambiguity blocks DB-canonical SQL-only runtime configuration.
- why it matters:
  - SQL-only cannot be considered canonical while runtime config still depends on legacy files or non-persisted defaults.

### 6.5 CSV Compatibility and Import/Export Role

- status: complete for this release line
- what is already done:
  - for this release line, CSV and dual mode remain supported compatibility runtime modes for existing installs.
  - SQL-only remains the target architecture and strict/canonical mode.
  - SQL export to CSV exists.
  - parity and reconciliation tooling exists.
  - CSV remains useful for compatibility runtime, backfill comparison, explicit import/export, and parity tooling.
  - focused SQL-only guard tests now exercise representative runtime routes and fail if they touch legacy CSV/JSON file guards.
  - validation now begins before app import and detects direct access to known compatibility runtime files independently of helper-level guards.
  - credentials such as `secret.json`, caches, explicit exports, backup archives, and explicit-operation temporary files are classified filesystem exceptions rather than SQL business state.
- future policy:
  - keep classifying CSV runtime paths as compatibility-mode paths, not SQL-only bugs.
  - any removal of CSV runtime support requires a later major-release decision.
- why it matters:
  - source-of-truth ambiguity is reduced only when compatibility CSV paths are clearly separated from strict SQL-only runtime behavior.

### 6.6 Release Hardening

- status: complete
- what is already done:
  - release CI runs the full unit suite, compile checks, SQL-only validation, a production Docker build, and in-container CLI/tool smoke tests without source path filters.
  - validation tooling exists.
  - diagnostic helpers exist.
  - the Phase 6 branch includes the required correctness and lifecycle hardening fixes.
  - Recalculate preview and execution now use the same stored pause-accounting input.
  - SQL-only printer rename, merge, and delete preserve DB-backed history and linked configuration without entering compatibility file/state paths.
  - Recalculate now exposes filament usage plus time, material, and total cost components in its jobs and preview tables.
  - canonical SQL reads for projects, settings, profiles, rates, printer identity, and system events surface persistence failures instead of presenting false empty state.
  - Recalculate is explicitly pricing-only for this release; Full Recompute is deferred until its semantics are deliberately designed.
  - README, changelog, and high-impact module comments describe the implemented storage, readiness, and compatibility contracts.
  - final release validation covers the broad test suite, SQL-only validator, readiness CLI, compile checks, and diff hygiene.
  - SQL-capable installer services use aligned `dual` writes and automatic SQL Reports reads, while CSV-only services keep a consistent CSV contract.
  - every printer-client mutation endpoint uses shared fail-closed API-key authentication.
  - manual backups use verified SQLite snapshots; strict SQL-only automatic scheduling is visibly unavailable rather than silently ignored.
  - SQL mutation failures surface without false success/audit events, and load-bearing numeric state must be finite and calculation-valid.
  - deliberate installer re-registration reactivates retired printers without changing their historical SQL identity.
- future work:
  - Full Recompute remains deferred until its data and pricing semantics are designed.
- why it matters:
  - the release is already feature-rich, but its operational contract is still sharper in code than in docs and guarantees.

## Current Position

Production v0.9.0 was independently verified on 2026-11-05, pinned to immutable index `sha256:57613a356037293310430551d11ef2025332873a4d18b2846e3f3e2cc1d28d25`, revision `c282f889`. Modes/Orders are deployed with Advanced default and additive empty 0009 Orders schema; prior printer pages, estimates and export remain verified. Four identities, readiness/integrity, 1,047 jobs and 316 objects are preserved; both SV07 rows remain Canceled/Unpriced. See [actual evidence](v0.9.0-deployment-2026-11-05.md). Later inventory/reporting main is queued for v0.10.0. SQL-only remains canonical; CSV/dual remain compatibility modes and Recalculate remains pricing-only. Read root LOGBOOK for actual outcomes.

## Current Active Work

v0.7.3 — Dashboard UX and Functional Reliability shipped from merged PR 59. Actual browser workflows cover all major pages, explicit spool pricing, project snapshots, recalculation, selection safety, current printer observations, accessibility and realistic history sizes. See [dashboard audit](dashboard-audit-v0.7.3.md) for implementation tests and [production evidence](v0.7.3-deployment-2026-11-03.md) for verified release/deployment results.

The lifecycle repair shipped and was verified in v0.7.4; v0.8.0 and v0.9.0 subsequently shipped their documented feature cohorts. Read root LOGBOOK for exact deployed digests and evidence; main can contain later, undeployed code.

## Completion-program state

The current owner directive and the [completion execution plan](completion-program.md)
supersede the older November 1 starting inventory. Retain every released storage,
pricing, identity, installer and thumbnail guarantee while completing the remaining
code cohorts through strict CI and normal solo-maintainer PR merges.

| Milestone | Current supported scope and actual state |
| --- | --- |
| A · Browser administration | Scoped local/SSH discovery and inspection, reviewed registration/repair/relocation, active-include and hook diagnostics, private backups and recoverable errors are implemented in PR 63. The service is separately authenticated and allowlisted. Unbounded discovery, initial Master bootstrap, macro edits and destructive host/lifecycle operations remain deliberately CLI-only. Optional listener activation is not part of the production Master deployment. |
| B · Interactive demo | PR 64 implements synthetic per-visitor workspaces, simulated activity, reset/expiry/quotas, isolated TLS/browser/container acceptance and default-deny boundaries. The owner explicitly requested isolated code/staging/release packaging only; no public endpoint or pending public-approval blocker belongs to this program. |
| C · Printer pages | Released in v0.8.0 with known/unknown telemetry, scoped history, outcomes and comparisons. |
| D/E · Modes and Orders | Released in v0.9.0 with reversible Advanced-default preferences and Business-gated project-backed financial/lifecycle data. |
| F/G · Reports and stock | Merged and tagged for v0.10.0; deployment evidence remains separate. Includes historical monthly filters/comparisons/coverage, safe outcome inference/cache invalidation and exact-milligram stock/correction/archive ledgers. |
| H/I · Estimates and project export | Released in v0.8.0: explicit spool units, signed reviewed planned items, atomic project mutations, canonical identity and consistent versioned SQL exports preserving original pricing. |

All final service-packaging code remains subject to its exact-head validation.
Public demo hosting, Full Recompute and undefined utilization/profit/ROI or optional
notification/import/printable-export workflows require separately established
requirements; they are not invented from printing cost or silently claimed complete.

## Next Planned Work

1. Monitor genuine printer events on verified v0.9.0; preserve historical prices and the two recovered SV07 canceled/unpriced rows without replaying recovery or recalculation.
2. Follow the [completion execution plan](completion-program.md) for PRs 62–70 and their dependencies. PR 60's v0.7.3 deployment handoff is already merged. PR 58's historical evidence and permanent governance are preserved in the current handoff replacement.
3. Retain v0.7.2 and later recovery artifacts and use validation, coordinated backup and immutable image digests for incremental releases. Full Recompute remains deferred. Physical hardware/assistive-technology and long-running performance certification remain separate validation limits.

## Deferred Feature Track

### Recalculate Full Recompute

- status: deferred, semantics not designed
- current contract:
  - Recalculate performs pricing recalculation only.
  - Full data recomputation is not a current-release feature or blocker.

### Projects Component Costs

- status: shipped; components and project-only adjusted snapshots verified in the v0.7 release line
- implemented accounting contract:
  - `Time Cost + Material Cost + Adjustment = Total Cost`
  - Adjustment is signed and reconciles persisted or explicit totals without inventing a labor/material split.
  - actual tracked/manual totals remain separate from active planned-item projections.

### Application modes

- status: merged and verified deployed in v0.9.0
- current requirements:
  - single global mode per KCD instance
  - reversible switching with no data deletion
  - Personal includes everyday history, Projects and basic pricing under the November 5 directive
  - Advanced is the existing-installation default
  - Business includes Advanced plus project-backed Orders from PR 66
  - mode preferences preserve history, pricing, profiles, assignments and identities
  - Orders enforce their Business-mode boundary; presentation modes do not replace service authentication
- See [Application modes](application-modes.md) for the operator workflow and root LOGBOOK for exact validation and deployment status.
- Merged PR 66 adds project-backed Orders and profitability on SQL-only installations; switching away from Business preserves saved orders and returns an explicit mode prompt for their direct URL.

### Historical reporting

- Milestone F's historical Reports implementation adds monthly completed/canceled/failed/unknown outcomes, completion rate, hours, filament length/weight, saved time/material/total cost components, adjacent-calendar-month changes and missing-value coverage. Printer and inclusive local-date filters apply to every section. SQL/CSV/dual use the same decimal calculations; SQL caches invalidate after historical edits.
- See [Monthly print outcomes](report-outcomes.md) for definitions and the distinction between saved measurements, estimated mass and unknown information. Utilization, availability, capacity, presets and business analytics require separate explicit data/denominator contracts; they are not inferred from print hours or advertised as implemented.

## Current Risks / Contradictions

- No normal-path SQL-only dependency on legacy runtime files is currently known; focused coverage is representative rather than exhaustive.
- compatibility CSV runtime paths remain supported for this release line, so SQL-only guardrails must keep that compatibility surface from becoming a strict-mode dependency.

## Definition of Done for the Current Phase

Phase 6 is complete for the current release contract because all of the following are true:

- SQL-only runtime does not perform normal-path reads from legacy runtime CSV/JSON files.
- runtime configuration used in SQL-only is DB-backed rather than file-backed or default-only.
- history, reports, settings, projects, diagnostics, and system events all behave consistently in SQL-only mode.
- CSV has a clearly defined non-runtime role, or an explicitly documented remaining compatibility role.
- validation/documentation accurately describe the supported SQL-only contract.
- the remaining intentionally incomplete areas are either finished or clearly de-scoped.

## Change Log Notes

Future roadmap edits should update this document instead of creating new competing roadmap files. If major architectural facts change, update `docs/unified_roadmap.md` first and refresh the audit only when a new repo-wide reconciliation is required.

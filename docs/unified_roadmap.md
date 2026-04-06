# Klipper Cost Dashboard - Unified Roadmap

## Purpose

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
  - It is not perfectly clean across the whole app because some runtime configuration paths still preserve file-backed compatibility behavior.

## Phase 5 - Installer SQL Awareness

- status: complete
- summary:
  - The installer became aware of CSV-only, SQL-capable, and dual/compat installs.
  - It can initialize the DB, apply migrations, import CSV into SQL, and sync printer mappings.
  - SQL-aware install flows were added without dropping backward compatibility.
- key outcome:
  - Existing installs can move toward SQL without a manual migration-only workflow.

## Phase 6 - SQL-Only Hardening and Canonicalization

This is the live execution phase.

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

- status: partial
- what is already done:
  - SQL-only blocks many file-backed reads and writes.
  - auto-create/runtime bootstrap behavior is blocked in SQL-only.
  - history and reports are forced to SQL in SQL-only mode.
  - runtime file-read migration behavior has been removed from `core/storage.py` for `load_settings()` and `load_display_settings()`.
- what remains:
  - broaden verification coverage so hidden CSV/JSON runtime reads are not reintroduced in SQL-only mode.
- why it matters:
  - SQL-only is not fully pure until runtime behavior is independent of legacy files.

### 6.3 Startup Verification

- status: partial
- what is already done:
  - `/health` has a SQL-safe path.
  - Moonraker diagnostics exist.
  - a lightweight SQL-only validation helper exists.
- what remains:
  - decide whether startup should fail fast when SQL state is incomplete or inconsistent.
  - add a clearer certification path for startup/runtime correctness beyond the current helper.
- why it matters:
  - SQL-only needs an explicit verification story, not just best-effort route hardening.

### 6.4 DB as Canonical Config

- status: in progress
- what is already done:
  - Moonraker URL can be edited in the UI and stored in the DB.
  - some SQL-only settings paths already use `user_settings`.
  - backup settings can persist in SQL-only.
  - SQL-only pricing now resolves from DB-backed settings and profile state instead of falling back to CSV or implicit default pricing during normal runtime.
- what remains:
  - tighten the remaining SQL-only configuration behavior that still uses explicit SQL-safe defaults instead of failing loudly when required DB-backed config is missing.
  - decide where SQL-only should use explicit SQL-safe defaults versus fail loudly when required DB-backed config is missing.
- why it matters:
  - SQL-only cannot be considered canonical while runtime config still depends on legacy files or non-persisted defaults.

### 6.5 CSV as Import/Export Only

- status: not complete
- what is already done:
  - SQL export to CSV exists.
  - parity and reconciliation tooling exists.
  - CSV remains useful for compatibility, backfill comparison, and explicit export.
- what remains:
  - decide whether CSV should remain a supported runtime compatibility mode or become a pure import/export artifact.
  - if SQL-only is the target end state, remove residual runtime dependence on CSV outside explicit tools.
- why it matters:
  - source-of-truth ambiguity remains until CSV has a clearly bounded role.

### 6.6 Release Hardening

- status: partial
- what is already done:
  - compile guards exist in CI.
  - validation tooling exists.
  - diagnostic helpers exist.
  - multiple hardening fixes have already landed on `main`.
- what remains:
  - reconcile stale documentation.
  - tighten validation coverage for SQL-only runtime behavior.
  - resolve the remaining inconsistent SQL-only subsystems before calling the release line clean.
- why it matters:
  - the release is already feature-rich, but its operational contract is still sharper in code than in docs and guarantees.

## Current Position

KCD is no longer a CSV-first app with experimental SQL on the side. SQL is already first-class for major read paths, migration tooling, installer behavior, and SQL-only enforcement. At the same time, the project is still in a transitional hardening phase: SQL-only is not fully pure, configuration migration is incomplete, docs are stale in places, and recalculation still contains an intentionally unfinished mode.

## Current Active Work

Finish SQL-only configuration migration by tightening the last SQL-only pricing/config cases that should fail loudly when DB-backed config is missing.

This is the strongest next task because it closes the clearest contradiction between the current architecture claims and the actual runtime behavior.

## Next Planned Work

1. Complete SQL-only pricing and configuration persistence so runtime behavior does not fall back to defaults where DB-backed settings are expected.
2. Consolidate and refresh documentation so README, changelog, and module comments match the actual post-`v0.3.0` architecture.
3. Tighten SQL-only validation and certification so startup/runtime guarantees are easier to prove.
4. Decide and document the long-term role of CSV: compatibility mode vs import/export only.
5. Revisit recalculation scope and either complete "full" recompute or explicitly de-scope it.

## Deferred Feature Track

### Modes

- status: deferred, planned-only
- why it is deferred:
  - The audit found no repo evidence that modes were implemented.
  - The current engineering priority is storage/source-of-truth hardening, not UI product segmentation.
  - Starting mode work before Phase 6 is stabilized would add another layer of gating on top of a still-transitional architecture.
- frozen constraints summary:
  - single global mode per KCD instance
  - reversible switching with no data deletion
  - personal mode hides Projects
  - business mode reveals optional profit-aware/business framing
  - backend/schema/storage remain identical across modes

## Current Risks / Contradictions

- SQL-only is not yet fully pure; some runtime file reads still remain in SQL-only-related paths.
- SQL-only pricing/config migration is incomplete.
- README and some module comments still describe earlier or conflicting architectural states.
- recalculation is intentionally incomplete because "full" recompute is still marked as coming soon.
- the repo still carries both compatibility and canonicalization goals at the same time, which creates ambiguity around CSV's final role.

## Definition of Done for the Current Phase

Phase 6 should be considered complete only when all of the following are true:

- SQL-only runtime does not perform normal-path reads from legacy runtime CSV/JSON files.
- runtime configuration used in SQL-only is DB-backed rather than file-backed or default-only.
- history, reports, settings, projects, diagnostics, and system events all behave consistently in SQL-only mode.
- CSV has a clearly defined non-runtime role, or an explicitly documented remaining compatibility role.
- validation/documentation accurately describe the supported SQL-only contract.
- the remaining intentionally incomplete areas are either finished or clearly de-scoped.

## Change Log Notes

Future roadmap edits should update this document instead of creating new competing roadmap files. If major architectural facts change, update `docs/unified_roadmap.md` first and refresh the audit only when a new repo-wide reconciliation is required.

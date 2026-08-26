# ADR: demo workspace isolation

- Status: Accepted for foundation
- Date: 2026-08-23
- Scope: KCD public-demo Phases 0–3

## Context

KCD supports csv, dual, and sql modes with process-wide configuration. Compatibility files, live printer state, thumbnails, backup paths, and some caches are global. SQL business repositories, however, obtain short-lived connections through one central function, and SQL report cache data is stored in the same database.

## Options

### A. Shared process with request-scoped `WorkspaceContext`

Strong data isolation is achievable by selecting a database and mutable paths before repository access, tracking connections through teardown, and failing when demo context is absent. Registry-backed renewable request leases act as shared readers; transactional state transitions for reset/expiration/deletion/cleanup act as exclusive lifecycle writers. Reset is an atomic directory replacement after connection closure. Resource and deployment cost are low, and existing business logic changes only at the central storage boundary. Testing burden centers on request/context leakage, lease recovery, cleanup races, and blocked global features. Accidental production fallback is controlled by explicit demo startup validation and no-context exceptions.

### B. Process/container per visitor

Existing globals become naturally isolated and operational reasoning is simpler per process, but container lifecycle, routing, cold starts, resource cost, cleanup, observability, and abuse capacity are substantially higher. Reset and authenticated persistent trials require orchestration. This remains the fallback if later phases must enable state that cannot accept explicit workspace identity.

### C. Shared process with per-request environment/path mutation

Rejected. Environment variables, module constants, repositories, and threads are process-global; swapping them creates cross-request races and silent production fallback risk.

## Decision

Choose Option A for SQL-only demo workspaces. The gate passes because databases are selected per request; connections are short-lived and teardown-tracked; repositories converge on the central connector; reports cache is inside each database; mutable workspace paths are explicit; unsafe global live/background/file/network features can be disabled; and normal mode preserves current behavior.

The Phase 3.5 gate replaces the original exclusive `O_EXCL` request lock. That lock safely excluded lifecycle work but also rejected every overlapping request in one browser. The registry now permits multiple bounded leases per active workspace. Lease acquisition and lifecycle state transition both use `BEGIN IMMEDIATE`, so exactly one side wins without holding a business-data transaction for the HTTP request. Heartbeats recover long requests; expiry recovers crashed processes. This is standard-library-only and behaves consistently across Windows development and Linux deployment.

Demo mode is a distinct process runtime and cannot mix with normal mode. It requires an absolute demo root plus `KCD_STORAGE_BACKEND=sql` and `KCD_REPORTS_BACKEND=sql`. Missing context never falls back to `data/kcd.db`. csv/dual demo operation is rejected. Live printer state, Moonraker, thumbnails, backup, installer, and shared export behavior remain disabled until later phases inject workspace identity or simulation.

## Consequences

The foundation supports isolated create/resume/touch/reset/expire/delete and future trials without one container per visitor. A lifecycle collision is fail-fast rather than queued: reset returns a retryable conflict, cleanup skips, and requests begun after a non-active state receive the session-state response. Phase 4 adds centralized route/action restrictions, CSRF, input validation, quotas, and abuse controls. If simulated/background work cannot carry explicit workspace context, that subsystem—not database selection—must use per-workspace workers or the Option B fallback.

Phase 4 retains Option A and adds a centralized, method-specific application policy. Cross-process fixed-window counters and quota reservations use the existing registry transaction boundary; they contain only hashed rate identities and internal workspace references. CSRF derives from the opaque session, application egress is denied centrally, and normal runtime bypasses all demo controls. This strengthens rather than changes the selected isolation architecture.

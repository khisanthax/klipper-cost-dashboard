# KCD ephemeral demo contract

Status: foundational, not public-ready. This contract describes the target public demo; Phase 4 must complete route enforcement and abuse controls before exposure.

## Workspace behavior

- `POST /demo/foundation/session` creates a cryptographically random internal workspace ID and 256-bit opaque bearer token, clones the configured immutable seed into a temporary directory, validates it, atomically publishes it, and sets only the token in an HttpOnly, SameSite=Lax cookie. HTTPS deployments must retain `Secure` cookies.
- Only the SHA-256 token hash is stored. A cookie never contains a path. A guessed workspace ID is not a session identity. Possession of the cookie is authorization, so TLS and normal cookie protections are mandatory.
- A valid cookie resumes the same active workspace. Missing, unknown, expired, non-active, or registry-error sessions fail closed and never select normal KCD data or another workspace.
- Ordinary requests in one workspace may overlap and retain their normal route semantics. Each holds a renewable cross-process registry lease through teardown. Lifecycle operations are exclusive: reset returns a retryable conflict while requests are active, cleanup skips active workspaces, and requests arriving after a lifecycle transition receive a controlled expired/session-state response.
- Activity is touched no more often than the configured interval (default 300 seconds). Expiration is the earlier of 24 hours since last activity and 72 hours since creation. Touch never extends the absolute deadline.
- Expiration makes a session unusable. Cleanup is an idempotent operator action in this batch; scheduling is deferred.
- Reset keeps the session token and workspace ID, closes request-owned connections, transactionally excludes new request leases, builds and validates a fresh seed clone, atomically replaces mutable data, increments `reset_count`, and leaves other workspaces unchanged.
- Workspace-owned state includes SQLite, report-cache rows/artifacts, exports, temporary files, uploads/assets, generated thumbnails, and future background-task state. The registry, immutable seeds, application code/templates/static assets, migrations, and operational logging are global.
- A workspace records its seed version. Existing workspaces keep that version until reset; reset uses the configured current seed. Seeds are opened read-only for validation and are never migrated in place.

## Visitor operations

The eventual demo may allow add/edit/delete for demo printers, filament profiles, hourly-rate profiles, safe pricing, history, projects, assignments, manual/planned jobs, display sorting/filtering, namespaced exports, simulated printers, and reset. Every mutation must use the resolved `WorkspaceContext`.

## Restricted operations

Demo mode must block real Moonraker/Klipper connections, installer actions, SSH, arbitrary remote URLs, private-network probing, shell/updater/container actions, backup or arbitrary-file restore, host-path selection, Docker-socket access, real API-key administration, and cross-workspace access. Printer-client routes, thumbnail network fetching, backups, diagnostics, Moonraker import/testing, and shared exports are blocked by the current foundation; Phase 4 owns complete policy enforcement.

Normal mode remains the default. With `KCD_RUNTIME_MODE` unset, csv/dual/sql storage, reports selection, authentication, migrations, backup paths, and existing relative `data/` paths retain their current contracts.

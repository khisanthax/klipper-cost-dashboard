# Demo isolation audit

## Architecture gate

Decision: **pass for shared-process, request-scoped SQL workspaces**, provided real-printer, backup, shared-file, installer, and network-probing surfaces remain disabled until explicitly refactored. SQL repositories converge on `core.db.connect_db()` and do not cache persistent connections. Reports cache is stored in the selected database. No database is opened at import time.

| Mutable resource | Current owner / initialization | Path source / lifetime | Global? | Request-scoped path | Required action and proof |
| --- | --- | --- | --- | --- | --- |
| Business SQLite | `core.db`, opened per repository operation | `data/kcd.db` from `core.config` | Path was process-global; handles short-lived | Yes, central `WorkspaceContext.database_path` | Implemented; A/B DB paths/data and teardown tested |
| Schema migrations | `core.db.apply_migrations` | immutable code SQL + selected DB | Code global, writes selected DB | Yes | Seed validation requires all migrations; seed never migrated |
| History repository | `core.history_repo`, `storage_backend` | CSV or `connect_db()` | Backend env global | SQL demo is request-scoped | Demo rejects csv/dual; A/B job seed tested |
| Printers/pricing | `pricing`, `printers`, `printer_lifecycle`, `storage` | JSON in compatibility modes or selected SQL | Compatibility paths global | SQL methods yes | Demo SQL-only; route policy blocks endpoint URLs |
| Filament/hourly profiles | `profiles`, `rates` | JSON globals or selected SQL | Compatibility globals | SQL methods yes | A/B profile mutations tested |
| Projects/batches | `projects` | JSON globals or selected SQL | Compatibility globals | SQL methods yes | A/B project mutation tested |
| Settings/display | `storage` and `user_settings` | JSON globals or selected SQL | Compatibility globals | SQL methods yes | Demo SQL-only; unsafe settings actions blocked |
| Reports cache | `reports_cache` | `report_cache` table in selected DB | No separate memory cache | Yes | Same cache key stores isolated payload; reset clears only A |
| Report parity artifacts | `reports_parity` constants | normal `data/` files | Process-global | Not yet | Block CLI/routes in demo until context-path refactor |
| Thumbnail files | `thumbnails._CACHE_ROOT` | env or `data/thumb_cache` | Process-global | Context has `uploads_path` but module not refactored | Disabled in demo; later namespace provider/cache |
| Thumbnail metadata cache | `thumbnails._meta_cache` | process memory, printer+filename key | Yes | Not safely keyed | Disabled in demo; later add workspace ID to key or per-context cache |
| Live printer state | `live._jobs`, loaded at import | memory; CSV mode `live_jobs.json` | Yes | No | Client routes blocked and start guard added; simulation later requires explicit workspace ID |
| API authentication | `core.config.API_KEY` at import | env or normal `secret.json` | Yes | Intentionally deployment-global | Demo does not read/create normal secret; client routes blocked |
| Automatic backups | app GET hook / `core.backup` | `data/backups`, module constants | Yes | No | Hook disabled and actions blocked in demo |
| Manual backup/snapshot | `core.backup` | normal data and OS temp | Yes | No | Blocked in demo |
| CSV export | `/download-csv`, `export_csv` | normal data temp/output | Yes | Context exposes `exports_path` | Route blocked until output refactor |
| Recalc audit | app | SQL `system_events` or `data/recalc_runs.jsonl` | Compatibility file global | SQL demo uses selected DB | SQL-only enforced |
| System events | `system_events` | JSONL compatibility or selected SQL | Compatibility global | SQL methods yes | Demo SQL-only |
| Import/backfill/verify reports | CLI modules | normal data constants | Yes | Not required for visitor runtime | Operator-only; block from public image/surface |
| Installer state | `installer.utils`, pricing compatibility | `data/install_state.json` | Yes | No | Installer unavailable in demo |
| OS temporary files | backup/export/lifecycle | OS temp or normal data | Mixed | Lifecycle temp is under workspace root; context has temp path | Block unrefactored operations |
| Registry | demo manager | `<demo-root>/registry/demo_registry.db` | Shared control plane by design | N/A | Separate from visitor DB; transactions/indexes tested |
| Seeds | demo manager | `<demo-root>/seeds/<version>` | Global immutable | Read-only source | Symlinks rejected; SQLite read-only integrity/schema validation; hash tested |
| Workspace lifecycle locks | demo manager | workspaces root, internal ID only | Cross-process filesystem primitive | Per workspace | O_EXCL lock plus registry states; no user paths |
| Flask current workspace | integration | `flask.g` + `ContextVar` | Request-local | Yes | Sequential and two-client isolation tested; no env rewriting |

## Import-time and process assumptions

`core.config` previously created `data/` and `secret.json` during import. Demo mode now avoids both. Flask remains a module-level application, backend/runtime configuration remains process-wide, and therefore a single process cannot mix normal and demo runtime. This is acceptable: visitor isolation is within one explicitly demo-configured process. Runtime environment variables are never changed per request.

All known SQL consumers call the central connection function. Existing callers close connections with `closing`, `with`, or the managed connection; teardown additionally closes request-tracked handles. Reset closes tracked handles before replacement. Background work has no request context and must receive an explicit context in later phases.

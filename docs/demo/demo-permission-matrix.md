# Demo permission matrix

Status: Phase 4 application policy. Every registered endpoint/method combination is listed. “Guard” means enforcement occurs before route code. All workspace mutations require session-bound CSRF, same-origin validation, the general and mutation rate classes, input validation, a request lease, and applicable quota reservation. Errors are visitor-safe HTML or structured JSON with redaction.

| Endpoint | Rule | Method | Current behavior / data | Network / file behavior | Demo class | Enforcement | CSRF | Rate / quota | Error / test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `demo_foundation_create` | `/demo/foundation/session` | POST | Clone immutable seed; set opaque cookie | Workspace-local clone | Public safe | Guard + manager | Bootstrap exempt; origin checked | Invalid-session | Safe JSON; session/cookie tests |
| `demo_foundation_reset` | `/demo/foundation/reset` | POST | Restore current workspace | Atomic contained replacement | Workspace lifecycle | Guard + exclusive transition | Required | General + lifecycle | 409 busy; reset/concurrency tests |
| `health` | `/health` | GET | Minimal availability only | None | Public safe | Guard replaces detailed route | No | None | `{status: available}` test |
| `static` | `/static/<path>` | GET | Immutable packaged assets | Packaged static read | Public safe | Explicit policy | No | None | Headers/policy completeness |
| `index` | `/` | GET | Dashboard/history from workspace SQL | No network; thumbnail fetch disabled | Workspace read | Guard + context | No | General | Actual-app read/isolation tests |
| `index` | `/` | POST | Complete/delete/recalculate history | Workspace SQL only | Workspace mutate | Action allowlist | Required | General + mutation; history capped because creation blocked | CSRF/A-B tests |
| `reports_page` | `/reports` | GET | Workspace reports/cache | Cache in workspace DB | Workspace read | Guard + context | No | General | Actual-app/cache isolation |
| `system_events_page` | `/system-events` | GET | Workspace-local audit events | Workspace DB | Workspace read | Guard + context | No | General | Policy completeness |
| `recalculate_page` | `/recalculate` | GET | Workspace history/pricing view | Workspace DB | Workspace read | Guard + context | No | General | Actual policy/read tests |
| `recalculate_preview` | `/recalculate/preview` | POST | Read-only cost preview | Workspace DB | Workspace mutate (unsafe method) | Explicit policy + validation | Required | General + mutation | CSRF/input tests |
| `recalculate_run` | `/recalculate/run` | POST | Update selected workspace jobs | Workspace DB; no JSONL in SQL demo | Workspace mutate | Explicit policy | Required | General + mutation | Existing recalc regressions |
| `projects_page` | `/projects` | GET | Workspace projects/jobs/plans | Workspace DB | Workspace read | Guard + context | No | General | Actual-app read |
| `projects_page` | `/projects` | POST | Approved project, assignment, manual/plan mutations | Workspace DB; multipart/G-code upload denied | Workspace mutate | Action allowlist; `upload_plan_gcode` blocked | Required | General + mutation; project create reservation | Quota/upload/CSRF tests |
| `settings_page` | `/settings` | GET | Safe workspace settings | Workspace DB; no probe | Workspace read | Guard + context | No | General | Actual policy |
| `settings_page` | `/settings` | POST | Approved local settings/profile/printer actions | Workspace DB; backup/Moonraker blocked | Workspace mutate | Settings action allowlist | Required | General + mutation; profile reservations | Block-before-call test |
| `settings_printers_page` | `/settings/printers` | GET | Local printer display/pricing | Stored remote destinations forbidden | Workspace read | Guard + context | No | General | Actual policy |
| `settings_printers_page` | `/settings/printers` | POST | Rename/merge/delete/pricing/mappings | No active host/URL fields accepted | Workspace mutate | Settings action allowlist + network-field deny | Required | General + mutation; no create-printer surface | Network-input tests |
| `settings_profiles_page` | `/settings/profiles` | GET | Filament/hourly profiles | Workspace DB | Workspace read | Guard + context | No | General | Actual-app read |
| `settings_profiles_page` | `/settings/profiles` | POST | Profile CRUD/mappings | Workspace DB | Workspace mutate | Settings action allowlist | Required | Profile quota reservations | Actual mutation/quota tests |
| `settings_other_page` | `/settings/other` | GET | Display settings; backup/export controls hidden | Workspace DB; no export/backup controls | Workspace read | Guard + context + template flag | No | General | Actual-app UI assertion |
| `settings_other_page` | `/settings/other` | POST | Column visibility only | Backup actions blocked | Workspace mutate | Settings action allowlist | Required | General + mutation | Backup side-effect mock |
| `settings_pause_page` | `/settings/pause` | GET | Pause pricing settings | Workspace DB | Workspace read | Guard + context | No | General | Policy completeness |
| `settings_pause_page` | `/settings/pause` | POST | Pause pricing settings | Workspace DB | Workspace mutate | Settings action allowlist | Required | General + mutation | Numeric/normal regression |
| `log_print` | `/log-print` | POST | Real printer history API | Printer client/API key | Blocked demo | Guard before API-key/route | N/A | Invalid-session | 403 JSON; network/side-effect tests |
| `job_start` | `/job-start` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `job_update` | `/job-update` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `job_pause` | `/job-pause` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `job_resume` | `/job-resume` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `job_cancel` | `/job-cancel` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `job_end` | `/job-end` | POST | Real live-job mutation | Real client state | Blocked demo | Guard before auth/route | N/A | Invalid-session | 403 JSON |
| `thumb_cache` | `/thumb/<printer>/<file>` | GET | Cached/remote printer thumbnail | Shared path and outbound fetch capable | Blocked demo | Guard; network primitive also denied | No | Invalid-session | Zero-call network test |
| `download_csv` | `/download-csv` | GET | Writes/serves normal export path | Shared file/send_file | Blocked demo | Guard before file generation | No | Invalid-session; export rate deferred | Zero-side-effect/policy test |
| `printer_diagnostics_page` | `/printer-diagnostics` | GET | Host/printer diagnostics | Moonraker probes and configuration disclosure | Operator only | Guard before route | No | Invalid-session | Zero-network policy test |

## Multiplexed action policy

Approved index actions: `complete_rows`, `recalc_costs`, `delete_rows`, `delete`.

Approved project actions: display update; project create/update/delete; assign/unassign/recalculate; manual-job create/update/delete; planned-item update/fulfill/delete/convert. `upload_plan_gcode` is blocked.

Approved settings actions: local printer delete/rename/merge/pricing; pause and column settings; filament/hourly profile CRUD and mappings. `backup_now`, backup scheduling, Moonraker save/test/import, and unknown actions are blocked before route code. A URL-like value or network-capable field name is independently rejected.

Operator CLI commands (`kcd demo list/cleanup/validate`, database migration/import/verify, installer, backup/restore, updater, shell and container operations) have no visitor HTTP mapping and remain operator-only. Adding any mapping without an explicit policy fails startup/tests.

## Inventory notes

The Phase 4 URL-map audit found 33 endpoint/method combinations: 3 public safe, 10 workspace read, 9 workspace mutate, 1 workspace lifecycle, 1 operator only, and 9 blocked demo. There are no conditionally registered visitor routes and no state-changing GET route. Flask's 400/403/404/405/413 handlers and the demo security/unexpected-error handlers are centralized in the request boundary; they are response handlers rather than URL-map entries. Unknown URLs receive the safe 404 handler, while a newly registered URL-map entry fails the startup completeness check until explicitly classified.

Installer, updater, backup/restore, database import/migration, registry/seed administration, cleanup, shell/subprocess, service/container, and host-path tools exist only as operator-side CLI or deployment code. They have no HTTP mapping. The policy explicitly blocks the HTTP surfaces that can reach printer clients, exports, backups, diagnostics, remote thumbnails, or Moonraker behavior before route logic runs.

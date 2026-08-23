# Demo permission matrix

Status: policy inventory for Phase 4 enforcement. “Foundation” means enforcement already exists in this batch.

| Surface | Route or operation | Current behavior | Demo policy | Enforcement layer | Phase | Notes / risk |
| --- | --- | --- | --- | --- | --- | --- |
| Session | `POST /demo/foundation/session` | Creates isolated clone/cookie | Allowed | Manager + Flask boundary | Foundation | Rate limiting and polished UX deferred |
| Session | `POST /demo/foundation/reset` | Atomic seed restore | Allowed | Manager + Flask boundary | Foundation | Requires valid active session |
| Health | `GET /health` | Runtime/readiness details | Read-only | Flask route | 4 | Reduce visitor-visible internals |
| Dashboard/history | `GET/POST /` | List, filter, recalc/delete history | Allowed | Request-scoped SQL + route policy | 4 | Verify every POST action explicitly |
| Reports | `GET /reports` | SQL/CSV reports and DB cache | Allowed | Request-scoped SQL | Foundation/4 | Cache lives in workspace DB |
| Projects | `GET/POST /projects` | Project/job/manual/plan CRUD | Allowed | Request-scoped SQL + route policy | Foundation/4 | Validate all form actions |
| Recalculate | `GET /recalculate`, `POST /recalculate/preview`, `POST /recalculate/run` | Preview/mutate job costs | Allowed | Request-scoped SQL + CSRF/policy | 4 | JSONL audit path must remain disabled in SQL demo |
| Settings | `GET/POST /settings*` safe pricing/profile/display actions | SQL configuration CRUD | Allowed | Request-scoped SQL + action allowlist | 4 | Foundation blocks known unsafe actions |
| Settings | printer rename/delete/profile mapping | SQL printer configuration | Allowed | Request-scoped SQL | 4 | No real endpoint configuration |
| Settings | `backup_now`, backup scheduling | Creates archives under normal data | Blocked server-side | Flask action policy | Foundation | No shared backup path |
| Settings | Moonraker save/test/import actions | Stores/probes arbitrary URL | Blocked server-side | Flask action policy | Foundation | SSRF/private-network risk |
| Diagnostics | `GET /printer-diagnostics` | Probes configured Moonraker | Hidden + blocked | Flask boundary | Foundation | Network disclosure/SSRF |
| Printer client API | `/log-print`, `/job-start`, `/job-update`, `/job-pause`, `/job-resume`, `/job-cancel`, `/job-end` | API-key authenticated real-printer mutations | Blocked server-side | Flask boundary | Foundation | Simulated provider replaces later |
| Thumbnails | `/thumb/...` and Moonraker fetch/cache | Shared disk + remote fetch + memory cache | Hidden + blocked | Flask boundary/helper | Foundation | Future provider must namespace cache |
| Export | `GET /download-csv` | Writes temporary CSV under normal data | Blocked now; namespaced later | Flask boundary | Foundation/4 | Later send workspace-scoped ephemeral file |
| Static/templates | `/static/*`, templates | Global immutable code assets | Read-only | Web server | 4 | No visitor writes |
| API credentials | `KCD_API_KEY`, `secret.json` administration | Global printer-client credential | Hidden + blocked | Startup + routes | Foundation/4 | Demo never creates normal `secret.json` |
| Reports cache CLI | `python -m kcd cache info/clear` | Active normal DB | Not applicable to visitors | Operator CLI | 4 | Demo operator commands need explicit context if exposed |
| Demo CLI | `python -m kcd demo list` | Lists internal short IDs/state | Not applicable | Operator CLI | Foundation | Never prints tokens |
| Demo CLI | `python -m kcd demo cleanup [--dry-run]` | Safe expiration cleanup | Not applicable | Operator CLI | Foundation | Scheduler deferred |
| Demo CLI | `python -m kcd demo validate` | Validates seed/registry/workspaces | Not applicable | Operator CLI | Foundation | Read-only except registry initialization |
| DB CLI | `db init/import/verify/backfill/readiness` | Mutates or audits installation data | Hidden + blocked | Deployment/CLI boundary | 4/7 | Must never target visitor data implicitly |
| Reports/history parity | CLI parity/regeneration | Reads/writes CSV/report artifacts | Hidden + blocked | Deployment/CLI boundary | 4/7 | Normal installation tooling only |
| Export CLI | `export csv --out` | Operator-selected path | Hidden + blocked | Deployment/CLI boundary | 4/7 | Host-path selection |
| Installer | `install.py`, `installer/*` | Systemd/client/remote installation | Hidden + blocked | Image/deployment boundary | 4/7 | Shell, SSH, filesystem, service mutation |
| Backup tool | `tools/kcd_backup.py`, restore workflows | Archives/restores persistent data | Hidden + blocked | Image/deployment boundary | 4/7 | Restore is never visitor-accessible |
| SQL validator/migration tools | `tools/validate_sql_only.py`, import/backfill scripts | Diagnostic/migration filesystem work | Not applicable | Image/deployment boundary | 7 | Operators only |
| Updater/container operations | installer/updater, Docker Compose/restart | Mutates code/process/container | Hidden + blocked | Deployment + no socket | 7 | Docker socket must not be mounted |
| Background auto-backup | GET request hook | May create shared archive | Blocked | Runtime-mode guard | Foundation | Scheduler absent in SQL demo |
| Live job state | `core.live._jobs` / `live_jobs.json` | Process-global or file-backed | Blocked; simulation later | Runtime guard + route block | Foundation/6 | Never shared between visitors |
| Temporary/report artifacts | `tempfile`, parity/recalc/export files | Mixed OS temp and normal data | Blocked or workspace-scoped | Context path + route policy | 4 | Only namespaced paths may be enabled |
| Uploads/assets | future uploaded files | No supported public upload surface now | Hidden | Route policy + context path | 4/5 | Validate type/size and containment later |

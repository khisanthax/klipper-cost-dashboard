# Klipper Cost Dashboard (KCD)

Klipper Cost Dashboard is a self-hosted web application that tracks print time,
filament usage, and cost for one or more Klipper-based 3D printers. It provides
history, reports, printer and pricing configuration, projects, system events,
pricing recalculation, and CSV export.

## Key Features

- Automatic print-time and filament tracking from Klipper clients
- Multi-printer history and reporting
- Per-printer pricing plus hourly-rate and filament profiles
- Pause-aware hourly billing
- Projects, manual jobs, and planned items
- Previewable pricing recalculation for historical jobs
- SQL-backed system events and runtime health checks
- Explicit CSV export, parity, import, and migration tooling

## Architecture

KCD has a central dashboard (the master) and Klipper clients. Clients send job
events to the master, which validates printer identity, calculates costs, and
persists the result using the configured storage mode.

KCD supports three runtime modes for this release line:

| Mode | Role |
| --- | --- |
| `csv` | Supported compatibility mode using legacy CSV/JSON runtime state. |
| `dual` | Supported compatibility and migration mode that keeps CSV behavior while also writing SQL. |
| `sql` | Strict/canonical mode using SQLite for business and runtime state. |

CSV and dual mode remain supported for existing installations. SQL-only is the
target architecture and the strictest operating contract.

## Docker Quick Start

```bash
mkdir -p kcd && cd kcd
curl -fsSL https://raw.githubusercontent.com/khisanthax/klipper-cost-dashboard/main/docker-compose.yml -o docker-compose.yml
mkdir -p data
docker compose up -d
```

The default compose file binds KCD to `127.0.0.1:6060`. Changing the mapping to
`6060:5000` exposes the dashboard to the LAN. The web UI does not provide user
login, so use an authenticated reverse proxy before exposing it beyond a trusted
network.

Common operations:

```bash
docker compose ps
docker compose logs --tail=200
docker compose pull
docker compose up -d
```

The image includes the documented CLI and operational tools. Run them through
the service container:

```bash
docker compose exec kcd python -m kcd --help
docker compose exec kcd python -m kcd db readiness
docker compose exec kcd python tools/validate_sql_only.py
docker compose exec kcd python tools/kcd_backup.py --keep 7
```

Persistent state is stored under `./data`, mounted at `/app/data`. Back up that
directory before upgrades. In CSV and dual mode this includes compatibility
CSV/JSON state; SQL-capable installations also use `data/kcd.db`.

For a source installation, clone the repository and run the interactive installer:

```bash
git clone https://github.com/khisanthax/klipper-cost-dashboard.git
cd klipper-cost-dashboard
python install.py
```

The installer supports master setup and local or remote Klipper clients. A
SQL-capable compatibility installation writes in `dual` mode and selects Reports
with `auto`; CSV-only installation keeps both writes and Reports on CSV. The
installer does not enable strict SQL-only automatically.

## Storage Modes

The default mode is `csv` when `KCD_STORAGE_BACKEND` is unset.

```bash
# Compatibility runtime
KCD_STORAGE_BACKEND=csv python app.py

# Compatibility runtime plus SQL writes
KCD_STORAGE_BACKEND=dual python app.py

# Strict SQL-only runtime
KCD_STORAGE_BACKEND=sql python app.py
```

In SQL-only mode, SQLite is canonical for:

- jobs and history
- printer identity and active configuration
- pricing and pause-accounting configuration
- hourly-rate and filament profiles and active mappings
- projects, assignments, manual jobs, and planned items
- display and runtime settings
- system events

SQL-only does not mean that the process performs literally zero filesystem I/O.
The following are deliberate exceptions to the SQL business-state contract:

- `data/secret.json` as the API-key credential fallback
- thumbnail cache files
- explicit CSV exports
- backup archives
- temporary files created for explicit operations

Set `KCD_API_KEY` to supply the printer-client API key through the environment.
When it is unset, KCD reads or creates `data/secret.json` as the credential
fallback.

Normal SQL-only runtime must not use legacy settings, display, project, profile,
history CSV, installer-state, or JSONL event files as its source of truth.

## Moving an Existing Installation to SQL-Only

Perform migration commands while the installation is still in CSV or dual mode.
Do not enable strict SQL-only until the readiness command succeeds.

For Docker installations, run each command below with
`docker compose exec kcd` before `python`, as shown in the Docker examples above.

1. Back up the current data directory. KCD can create an archive with:

   ```bash
   python tools/kcd_backup.py --keep 7
   ```

2. Initialize SQLite and apply migrations:

   ```bash
   python -m kcd db init
   ```

3. Import legacy CSV/JSON state:

   ```bash
   python -m kcd db import
   ```

   Use `--overwrite` only when existing imported SQL rows should be replaced.

4. Compare legacy history with SQL where parity is expected:

   ```bash
   python -m kcd db verify
   ```

5. Run the SQL-only preflight:

   ```bash
   python -m kcd db readiness
   ```

6. Resolve every reported failure. Readiness checks database connectivity,
   migrations, required tables, pause billing policy, active printers, and their
   DB-backed pricing/profile configuration.

7. Set the strict runtime mode and restart KCD:

   ```bash
   export KCD_STORAGE_BACKEND=sql
   python app.py
   ```

8. Confirm runtime readiness at `/health`. Docker uses this endpoint for its
   healthcheck.

Strict SQL-only startup fail-fast is enabled by default. If an administrator must
start the application temporarily to diagnose or repair incomplete state,
`KCD_SQL_ONLY_FAIL_FAST=0` disables startup enforcement. This is an emergency or
diagnostic escape hatch, not the recommended steady-state configuration. Run
`python -m kcd db readiness` again before restoring strict startup.

Printer-client mutation routes require the configured `X-API-Key`. If KCD cannot
establish a server API key, those routes fail closed rather than accepting
unauthenticated requests.

## SQL Validation and Export

Run representative startup and route-level filesystem isolation validation with:

```bash
python tools/validate_sql_only.py
```

Export SQL history explicitly:

```bash
python -m kcd export csv --from sql --out data/print_costs.csv --overwrite
```

SQL may legitimately contain jobs imported from Moonraker that are absent from a
legacy CSV. In that case parity tools report dataset differences rather than
implying that SQL should discard the additional rows.

```bash
python -m kcd reports parity --range 90d --dump-job-diff
python -m kcd reports parity --range 90d --regen-csv-from-sql
python -m kcd history parity --limit 200
```

SQL Reports uses a DB-backed cache. The default TTL is 300 seconds. Set
`KCD_REPORTS_CACHE_TTL_SECONDS=0` to disable it.

```bash
python -m kcd cache info
python -m kcd cache clear
```

## Printer Retirement

Deleting a printer retires it from active configuration. KCD no longer accepts
new jobs for that identity. Existing jobs and events remain available, and their
historical printer references remain valid. Deliberately reinstalling or
re-registering that printer through the installer reactivates the same historical
identity; pricing must then be configured before SQL-only readiness succeeds.

## Backups

`Backup now` and `python tools/kcd_backup.py` create a transactionally consistent
SQLite snapshot and exclude live WAL/SHM files and nested backup archives.
Archives are created with owner-only `0600` permissions because they may contain
credentials.
Automatic backup scheduling is unavailable in strict SQL-only mode and is
disabled visibly in Settings. Backup archives include `secret.json` when present,
so protect them as credentials-bearing files.

## Recalculate Center

Recalculate Center performs pricing recalculation for selected historical jobs.
It supports previews and shows:

- hours
- filament usage
- time cost
- material cost
- total cost
- supported hourly-rate, filament-profile, and per-run pricing overrides

Recalculate does not rewrite job identity or raw history. Full data recomputation
is not part of the current release contract and remains deferred until its
semantics are designed.

## Projects

Projects group tracked jobs, manual work, and planned items. Actual and projected
costs are reported with the same component equation:

`Time Cost + Material Cost + Adjustment = Total Cost`

Adjustment is signed and may be positive or negative. For tracked jobs it preserves
any difference between stored component costs and the stored total. For manual and
planned items with an explicit total override, the ordinary time and material costs
remain visible and Adjustment reconciles those components to the exact override.
Component values are aggregated at full precision and rounded only for display.

## Web Interface

- **Dashboard / History**: current state plus sortable, filterable job history
- **Reports**: date-range and printer summaries
- **Settings**: printers, pricing, profiles, display, pause accounting, and backups
- **Projects**: grouped tracked jobs, manual jobs, and planned items
- **Recalculate**: previewable pricing updates for historical jobs
- **System Events**: operational warnings, failures, and meaningful activity

If duration or thumbnails are missing, open **Settings > Printers > Diagnostics**
and verify the configured Moonraker URL and response.

## Screenshots

### Dashboard

![Dashboard overview](docs/images/dashboard-overview.png)

### Reports

![Reports](docs/images/reports.png)

### Projects

![Projects](docs/images/projects.png)

### Recalculate Center

![Recalculate Center](docs/images/recalculate-center.png)

### Settings

![Printer settings](docs/images/settings-printers.png)
![Profile settings](docs/images/settings-profiles.png)
![Pause accounting settings](docs/images/settings-pause.png)

## Project Status

KCD is actively developed. Runtime compatibility and upgrade safety matter, but
APIs and internal formats may continue to evolve. Use GitHub issues for bug reports
and focused feature proposals.

Pull requests intended for release must pass the unfiltered **Release Validation**
workflow, including the full unit suite, compile checks, SQL-only isolation,
production Docker build, and in-container CLI/tool smoke tests, before merge.

# Project Overview
- Flask-based Klipper print cost dashboard: ingests printer logs via `/log-print`, stores CSV in `data/`, shows history, reports, settings; supports live job tracking.

# Architecture Summary
- `app.py`: Flask routes for API (`/log-print`, live job control), health, dashboard, reports, settings, CSV download; renders templates.
- `core/config.py`: paths (`data/` CSV/settings/display/secret/profiles), defaults, headers/labels, printer colors, API key ensure.
- `core/storage.py`: JSON/CSV helpers, settings/display/profile persistence, API key generation, installer state helpers.
- `core/pricing.py`: pricing resolution (printer settings + profiles), cost computations, printer rename/merge, live cost helpers.
- `core/live.py`: live job state persistence (`data/live_jobs.json`), pause/resume/cancel/end, enrichment with costs/estimates.
- `core/profiles.py`: filament profile CRUD and printer-profile mappings (`data/profiles.json`).
- `core/reports.py`: date range parsing, summary aggregation (monthly, top printers, per material/profile, per printer for cards).
- Templates: `templates/base.html`, `index.html`, `reports.html`, `settings.html` (backup files exist). Data files live in `data/`.
- Installer: `installer/setup.py`, `installer/remote.py`, `installer/utils.py`; `install.py` top-level helper.

# Coding Conventions
- Python: keep modules modular under `core`; prefer functions over large monoliths; avoid circular imports (lazy import when needed).
- Formatting: PEP8-ish, clear naming; JSON persisted via `json.dump(..., indent=2)`; CSV headers must align with `core/config.HEADERS`.
- Defaults: use `DEFAULT_PRICING` when settings missing; ensure data files exist before read; guard file I/O with try/except returning safe defaults.
- Avoid rewriting API keys once set; preserve backward compatibility by filling missing fields when reading rows.
- Templates: keep Bootstrap 5 styling where present; avoid introducing inconsistent styles; keep ASCII text and readable labels.

# Important Variables and Settings
- Data paths: `data/print_costs.csv`, `data/settings.json`, `data/display.json`, `data/secret.json`, `data/profiles.json`, `data/live_jobs.json`.
- API key: stored in `secret.json` via `core.storage.ensure_api_key`; do not overwrite existing key; use header `X-API-Key` for `/log-print`.
- Printers: known defaults include `SV08`, `SV07`, `Ender5P`; `PRINTER_COLORS` map lives in `core/config.py`.
- Pricing fields: `rate_per_hour`, `filament_mode` (`per_meter`, `per_gram`, `per_kg`), `filament_rate`, `grams_per_meter`.
- Profiles: stored in `profiles.json` with mappings printer->profile; fallback to printer pricing when mapping missing.
- Live jobs: stored in `live_jobs.json` keyed by printer; status values include `printing`, `paused`, `canceled`, `completed`.
- Installer state file: `data/install_state.json` (clients, etc.).

# Installer Rules
- Do not overwrite existing API keys; generate only when missing.
- Ensure data dir exists before writing; create default settings/display files on first run.
- Track install state in `install_state.json` (e.g., clients list); update when renaming/merging printers.
- Client/master interaction: clients send jobs to `/log-print`; installer should register printers and preserve settings; merges/renames must update settings, registry, and CSV.
- Updates should be additive/non-destructive to existing data files.

# UI/UX Rules
- Navigation: History (dashboard), Reports, Settings, Download CSV; keep clear labels.
- Dashboard: printer overview cards (status badges, last job, today hours/cost), summary stats, filters, charts, print history table with delete controls.
- Reports: date filters (manual + quick ranges), summary cards, monthly/top printer/material/profile breakdown tables.
- Settings: printer pricing forms, column visibility controls, profile mappings and CRUD; keep headings readable and consistent.
- Maintain Bootstrap 5 layout where used; avoid garbled characters; keep prompts and alerts concise.

# Rules for Codex
- Always read this file before making changes; update it when project conventions/architecture/decisions change.
- Ask user before modifying files; provide review findings first.
- Respect existing architecture (Flask + core modules); do not overwrite API keys or data unless intended.
- Keep changes ASCII unless justified; add minimal clarifying comments when code is non-obvious.
- When adding features touching CSV/schema, ensure headers stay in sync across config, storage, pricing, templates.
- Be cautious in read-only sandbox; request approval before write; never revert user changes.

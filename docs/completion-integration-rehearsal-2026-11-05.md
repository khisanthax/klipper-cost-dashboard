# Combined candidate rehearsal — November 5, 2026

This is an isolated integration candidate on `resume/completion-integration-rehearsal`, based on main `7892765c4862d2cc2a2ee637ed01a4154b333535`. It does not merge protected main, publish an image, or deploy production. Required reviews and post-merge release gates remain in force.

## Included candidates

| PR | Scope | Exact source head |
| --- | --- | --- |
| 61 | Cancellation identity and reconciliation | `c19f04cca5c8e21208033f3d884a66806d052378` |
| 62 | Printer detail and unknown measurements | `edffb3d055f265749a5b9f66e6cec0ff4729ce69` |
| 63 | Installer web administration | `610e23802b893c8d1542b5a3a0c038cdada14ca1` |
| 64 | Isolated demo and policy-backed controls | `ea9cfd6b4358cab50905c01d3e84aa32ffdef6f2` |
| 65 | Modes and public-demo presentation boundary | `e795b1ced434be0bf85150498d6cd5de13ee9663` |
| 66 | Project-backed Orders, stacked on Modes | `558e582523888244bd6b48d2798b5c8aa238ba3c` |
| 67 | Physical spool inventory and recovery | `4ecd7dd1c95622cbd6b5ec508bc4d218a95e70d6` |
| 68 | Reviewed estimates and project mutation safety | `e1f712f23916d1fc4fcdc31be8daf4542a899f20` |
| 69 | Consistent project export and canonical identity | `dc9dc94cbf441fa0e4a272a9ac5a1b48c3ae755e` |
| 70 | Historical outcomes, imported status and thumbnail concurrency | `7eeffcc734a6140af8dda150bbb133adcb27a5d8` |
| 71 | Handoff and seeded full-state upgrade/rollback validation | `4b70ddce9da72b8c302f87cf66f2339f9ab8ba7f` |

Merge resolutions retained every feature's browser route/workflow, both demo and web-admin checks, SSH acceptance, the isolated demo container smoke and both pinned v0.7.3/v0.7.2 upgrade-and-rollback paths. Orders remain Business-only. Personal includes Projects; Inventory and export navigation follow Advanced presentation. New demo surfaces remain explicitly denied. Normal printer-card Clear controls retain their stacking above stretched links.

## Confirmed repairs during rehearsal

* SQL planned/manual edits rewrote complete tables from stale snapshots, erasing concurrent creates or conversions. Targeted row mutations and atomic conversion now preserve unrelated data. SQL cleanup does not treat different read snapshots as deletion evidence.
* Reviewed estimate display rounding differed from the persisted override. The saved reviewed total now agrees with the displayed component sum while preserving raw assumptions.
* Project display names and legacy numeric aliases could shadow canonical UIDs. Planned/manual creation and project export now prioritize canonical identities.
* Public demo welcome pages have no mutable workspace. The Modes context processor attempted a SQL setting read and correctly hit the isolation guard. Public presentation now uses its default without reading mutable storage; bound sessions retain scoped settings.
* New denied demo routes and the denied mode/Clear actions were still represented by clickable controls. Policy-backed template guards hide them while keeping Projects, profiles and sample pricing usable.
* Orders browser checks retained direct SQLite connections after their context blocks, preventing Windows fixture cleanup. They now explicitly close those readers.

## Local acceptance

Run unit commands as `python -m unittest discover -s tests -p <file> -v`. Disposable data and test-only credentials were used throughout.

| Workflow | Result | Evidence |
| --- | --- | --- |
| SQL-only startup/routes and filesystem isolation | Passed | `python tools/validate_sql_only.py`; zero legacy accesses |
| Project identity resolution | Passed | `test_project_identity_resolution.py`, 2 cases |
| Reversible modes and public-demo no-storage context | Passed | `test_application_mode.py`, 3 cases |
| Concurrent plan/manual changes, rollback and retry | Passed | `test_planned_item_concurrency.py`, 6 cases |
| Canonical export, WAL snapshot, units and HTTP download | Passed | `test_project_export.py`, 4 cases |
| Orders lifecycle, unknown/zero revenue and mode preservation | Passed | `test_business_orders.py`, 7 cases |
| Printer history, pagination and missing telemetry | Passed | `test_printer_detail.py`, 6 cases; one host IANA-data skip |
| Reports, measured values and unknown costs | Passed | `test_report_outcomes.py`, 8 cases; one host IANA-data skip |
| Imported terminal outcomes | Passed | `test_import_inference.py`, 6 cases |
| Thumbnail retention, recovery/delete race and backups | Passed | `test_thumbnail_archive.py`, 12 cases |
| Consistent backup, inventory and Order restoration | Passed | `test_backup_consistency.py`, 9 cases; one Windows POSIX-permission skip |
| Preliminary combined demo policy, quotas and isolation | Passed | 21 cases at B's earlier `5824757` integration; final source is additionally exercised through actual HTTPS below and requires full CI |
| Normal browser workflows, 100 history jobs | Passed | `python tools/browser_dashboard_audit.py --workflows-only --history-jobs 100` |
| Actual HTTPS demo, desktop/mobile | Passed | `python tools/browser_demo_regression.py`: denied controls absent, direct denials, secure cookies/HSTS, host/HTTP rejection, scoped telemetry, Projects/profiles/pricing, visitor isolation and reset |
| Upgrade-preservation assertions and new-feature fixtures | Passed | `test_upgrade_preservation.py`, 4 cases; actual Docker execution remains a separate gate |
| Complete Linux release/image/SSH/systemd/dual-baseline rollback validation | Not Tested locally | Requires the exact combined-head GitHub Release Validation run; local Docker Desktop engine is unavailable |
| New release or production deployment | Not Tested | This branch performs neither |

Full CI must cover every final source together; separate PR CI is not a substitute for that gate.

`python -m compileall -q app.py core installer kcd tools tests` and `git diff --check origin/main` validate compilation and merge whitespace. Native device, screen-reader, native-toolbar zoom and sustained production load are outside this local rehearsal.

## Failure evidence and reproduction

The first sandbox browser attempt could not create loopback sockets (Windows 10013); the authorized isolated browser runs used escalation for those fixtures. The initial combined normal browser completed product assertions but failed temporary-directory cleanup because of the SQLite reader leak. After the reader correction it exited successfully.

The combined HTTPS demo reproduced the public-page storage-context failure before its fix. Its trace and server log were preserved locally under ignored `browser-artifacts/demo-public-context-failure/`. Successful normal workflow captures are under `browser-artifacts/dashboard-workflows/`; successful HTTPS captures are under `browser-artifacts/demo/`. These contain only disposable synthetic state. GitHub CI uploads its own exact-head `browser-regression-<run-id>` artifact.

Initial [Linux Release Validation 42044092307](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42044092307) ran 376 tests and found one merge-only default-navigation regression. Inventory's explicit default-true predicate had been nested inside the Recalculate Advanced block. Moving Inventory outside that outer block preserves its own mode/demo rules and its standalone default. Compile CI passed; full downstream validation requires the corrected-head run.

## Production and promotion

No live printer commands, database writes, credential changes, service restarts or deployments were made by this rehearsal. The separately verified [November 5 read-only production record](completion-program-production-2026-11-05.md) remains authoritative for v0.7.3, preserved SV07 cancellations and persistent thumbnail objects.

Before release, inspect the exact combined-head CI, obtain required eligible non-author reviews, and integrate through the established release process. Preserve coordinated backups and immutable rollback images. Public demo exposure additionally requires approval of a concrete hosting and network-security configuration.

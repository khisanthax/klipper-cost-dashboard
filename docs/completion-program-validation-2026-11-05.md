# KCD completion-program acceptance record

> Historical November 5 completion-program record. Current November 7 production is v0.12.0; see [LOGBOOK](../LOGBOOK.md) and [actual measurement/pricing deployment](v0.12.0-deployment-2026-11-07.md). The earlier program outcomes below are retained as dated evidence.



**Current outcome:** Every existing source PR is resolved through normal protected
merges under the [solo-maintainer policy](solo-maintainer-release-policy.md).
Production is independently verified v0.11.0 at 23977013; its
[actual deployment and complete matrix](v0.11.0-deployment-2026-11-05.md) records
image, backup, rollback, live checks and intentional optional-service boundaries.
Required PRs and strict CI remain enforced with zero human approvals.

The initial candidate table and combined rehearsal below are **historical**:
they preceded the serial final integrations and releases. Their passing fixtures
do not substitute for the later exact-head CI or actual production evidence.
The original read-only baseline was v0.7.3; verified deployments subsequently
completed v0.7.4, v0.8.0, v0.9.0, v0.10.0 and v0.11.0. Demo remains isolated by
explicit owner choice; optional administration/demo services are inactive.

## Initial candidate results (historical)

| PR | Workstream | Verified implementation and regression scope | Result / evidence |
| --- | --- | --- | --- |
| 58 → 71 | Permanent handoff | Recovered root AGENTS instructions and historical v0.7.2 evidence; corrected roadmap/current production statements; superseded stale PR with linked handoff | Passed; PR 58 closed, replacement PR 71 merged |
| 61 | Lifecycle | Measured cancel filament, unknown consumption, per-attempt identity token, duplicate/delayed event guards, atomic history/live/audit rollback; existing recovery retained | Passed; code c19f04c, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42039070997) |
| 62 | Printer pages | Missing telemetry remains unknown; known live components, stable equal-timestamp pagination, scoped history and browser navigation; demo does not advertise unavailable detail pages | Passed; edffb3d, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041207299) |
| 63 | Browser administration | Scoped shared CLI inspection, active includes/custom hooks, exact allowed roots/URLs, private backup previews, redacted errors; restacked on latest 61 | Passed; 610e238, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42039237350) |
| 64 | Isolated demo | Allowed-host/proxy-peer enforcement, real loopback TLS, workspace interruption/capacity recovery, creation limits and denial of new privileged routes | Passed; ea9cfd6, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42043427091); public exposure remains gated |
| 65 | Modes | Personal includes Projects; Advanced default; cross-browser preference and unchanged data; public demo welcome avoids unbound SQL access | Passed; e795b1c, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42043117786) |
| 66 | Orders | Updated modes dependency, reopen/delete transitions, completion timestamps, unknown versus zero revenue, signed adjustments, preserved jobs and denied demo controls; SQL handles closed | Passed; 558e582, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42043220742) |
| 67 | Spools | Additive 0011 upgrade, exact-milligram linked corrections, replay/stale-form protection, estimates without deductions, archive/reactivation, backup restore and mode/demo navigation | Passed; 4ecd7dd, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041905497) |
| 68 | Estimates | Spool units, bounded metadata, signed reviewed plans, rounded preview persistence, atomic plan/manual CRUD and conversion, unknown-weight protection, canonical project-UID collision safety and demo guards | Passed; e1f712f, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041070343) |
| 69 | Export | One WAL snapshot, original-price whitelist, overrides, units/null/precision, numeric UID collision safety, unrelated-project exclusion, mode/demo guards and actual browser attachment | Passed; dc9dc94, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42043280579) |
| 70 | Reports | Date/printer filters, month comparisons, measured/estimated mass coverage, separate saved cost components, content-based invalidation, terminal import precedence and thumbnail/deletion race repair | Passed; 7eeffcc, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041568018) |

Focused estimate/project checks cover rounding, expired/tampered/replayed reviews,
oversized/malformed uploads, failed database writes, overlapping edits/creates,
conversion rollback and concurrent retries. SQL-only isolation remains required.
Additional real-SQL cases verify that a display name or legacy numeric alias
cannot redirect a canonical project UID to an unrelated project.
The synthetic RaRa case preserves 15 jobs, 907.294 m, 2,721.882 g and $40.83 at
$0/hour and $15/kg. No production recalculation was rerun.

Each linked full Release Validation run retains a `browser-regression-RUN_ID`
artifact with synthetic screenshots, traces and server/test evidence. Local
combined screenshots were also inspected for readable mobile and 200% CSS-zoom
estimate previews. Private production snapshots/credentials are not release or
CI artifacts.

## Final integrated release acceptance

| Cohort | Exact source | Final release CI | Actual outcome |
| --- | --- | --- | --- |
| v0.7.4 lifecycle/governance 61/71 | 237ccc88 | [42071334980](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42071334980) | [Verified deployment/rollback](v0.7.4-deployment-2026-11-05.md); both SV07 rows Canceled/Unpriced |
| v0.8.0 estimates/export/printers 68/69/62 | b154137b | [42080058742](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42080058742) | [Verified live pages/stateless estimate/private export/dry-runs](v0.8.0-deployment-2026-11-05.md) |
| v0.9.0 modes/Orders 65/66 | c282f889 | [42084641768](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42084641768) | [Verified additive 0009, preserved Advanced/Business guard](v0.9.0-deployment-2026-11-05.md) |
| v0.10.0 inventory/reports 67/70 | d5df3a81 | [42087043445](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42087043445) | [Verified 0010/11, live report SQL comparisons, empty inventory](v0.10.0-deployment-2026-11-05.md) |
| v0.11.0 optional administration/demo 63/64 | 23977013 | [42092424249](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42092424249) | [382 tests, isolated staging and verified normal Master deployment](v0.11.0-deployment-2026-11-05.md); services inactive, no new listener/public demo |

All cohorts retained the original 1,047 existing jobs, 316 objects and 435 archived references
through coordinated fresh backups, isolated restores and image rollback checks.
Production Orders/inventory writes, historical recalculation, physical G-code and
printer restarts were not performed merely for acceptance. Synthetic CI artifacts
and private real-browser/dry-run evidence are distinguished in each release report.

The final safe-window wait also included two naturally completed prints, matched to Moonraker history 00017B/00038C and normal KCD events without correction. The refreshed final baseline is **1,049 jobs, 317 objects and 436 archived references**; all original 1,047 historical rows remained unchanged. Legacy missing SQL start timestamps were left unknown, with printer/filename/unique end-time evidence used for those two matches. The earlier deferral report remains a historical snapshot, superseded by verified deployment.

## Combined integration rehearsal (historical)

A separate `resume/completion-integration-rehearsal` branch combines candidates
without merging protected main. Mechanical overlaps retain every scoped browser
workflow, additive migration and logbook entry. Integration fixes are sent back to
their owning PRs. The corrected `7362a0e` rehearsal passed [full Linux validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42069345011): 376 tests plus normal/TLS demo browser, security, installer, Docker and seeded Orders/inventory upgrade/rollback. The first run caught a navigation composition default and a demo-session test-fixture omission; both were corrected before this pass. Local Docker was unavailable; actual image checks ran in Linux CI.

The image harness now tests pinned v0.7.3 and v0.7.2 baselines through actual
upgrade and rollback, including readiness, core SQL preservation, archived image
bytes and post-upgrade table preservation. These are disposable environments.
The tooling-only PR 71 head `4ffb6bd` passed [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42040382282),
including 226 Linux tests and both actual upgrade/rollback paths. The combined
feature branch additionally checks the additive feature migrations.

Image rollback verifies startup and data preservation. An older UI cannot manage
new Orders or inventory features. Retain their tables and foreign-key constraints;
do not restore an older backup over newer jobs or remove protected project links
to make an old action succeed. Orders use `ON DELETE RESTRICT`, preserving their
project associations even when the older image lacks the Orders interface.

## Production and operational boundaries

The [read-only baseline](completion-program-production-2026-11-05.md) passed SQL
integrity, all five readiness checks, four loaded printer identities, preservation
of both canceled/null-cost SV07 rows and 316 permanent object hashes. There are
1,047 jobs and 435 archived references. That initial check made no production
changes. The later [v0.7.4 deployment](v0.7.4-deployment-2026-11-05.md) replaced only
KCD after verified backup/restore; history, configuration and objects remained unchanged.

| Operation | Status | Required next condition |
| --- | --- | --- |
| Required main review policy | Updated | Zero human approvals; inspect final diff/migrations, resolve findings and require strict validate/compile checks |
| Main integration / release / production deployment | Complete | Every source PR resolved; verified v0.7.4 through v0.11.0 deployment evidence above. Optional administration/demo code shipped; separate services remain inactive |
| Public demo exposure | Kept isolated by owner decision | No public endpoint or production demo mode included; any future exposure is a separate target-specific security/operational decision |
| Optional administration service | Intentionally inactive | Private targets/strong separate credential/least privilege/loopback forwarding and idle-window preview needed for later activation |
| Physical Pi fresh installation / real power failure / native toolbar zoom / assistive-technology certification | Not tested here | Separate physical/environment validation; automated viewport/CSS zoom and axe results do not establish these |
| Utilization / profit / ROI without defined availability or revenue data | Not implemented by inference | Approved semantics and valid source data; cost alone is insufficient |

The running v0.11.0 digest and checks are recorded in its linked actual evidence.
Existing v0.7.3 and earlier rollback evidence remains intact. A passing candidate does not replace
the repository's required CI or final engineering inspection.

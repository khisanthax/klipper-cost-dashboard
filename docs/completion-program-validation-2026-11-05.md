# KCD completion-program acceptance record


**Policy update:** The owner-authorized [solo-maintainer policy](solo-maintainer-release-policy.md) supersedes all earlier human-review blockers below. Required PRs and strict CI remain enforced. PR 61 is merged; release/deployment validation is continuing.
This record distinguishes candidate validation from production deployment.
Production remains verified v0.7.3. The existing feature PRs have not been merged
or released. The [execution plan](completion-program.md) defines dependencies and
the incremental release procedure.

## Candidate results

| PR | Workstream | Verified implementation and regression scope | Result / evidence |
| --- | --- | --- | --- |
| 58 → 71 | Permanent handoff | Recovered root AGENTS instructions and historical v0.7.2 evidence; corrected roadmap/current production statements; superseded stale PR with linked handoff | Passed; PR 58 closed, replacement PR 71 open |
| 61 | Lifecycle | Measured cancel filament, unknown consumption, per-attempt identity token, duplicate/delayed event guards, atomic history/live/audit rollback; existing recovery retained | Passed; code c19f04c, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42039070997) |
| 62 | Printer pages | Missing telemetry remains unknown; known live components, stable equal-timestamp pagination, scoped history and browser navigation; demo does not advertise unavailable detail pages | Passed; edffb3d, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041207299) |
| 63 | Browser administration | Scoped shared CLI inspection, active includes/custom hooks, exact allowed roots/URLs, private backup previews, redacted errors; restacked on latest 61 | Passed; 610e238, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42039237350) |
| 64 | Isolated demo | Allowed-host/proxy-peer enforcement, real loopback TLS, workspace interruption/capacity recovery, explicit denial of new privileged routes | Final combined-policy validation pending |
| 65 | Modes | Personal includes Projects; Advanced default; cross-browser preference and unchanged historical/project data; demo mode changes hidden and denied | Passed; 3e7a903, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041242418) |
| 66 | Orders | Updated modes dependency, reopen/delete transitions, completion timestamps, unknown versus zero revenue, signed adjustments and preserved jobs; denied demo controls hidden; browser SQL handles closed | Passed; f8aaad7, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041374572) |
| 67 | Spools | Additive 0011 upgrade, exact-milligram linked corrections, replay/stale-form protection, estimates without deductions, archive/reactivation, backup restore and mode/demo navigation | Passed; 4ecd7dd, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041905497) |
| 68 | Estimates | Spool units, bounded metadata, signed reviewed plans, rounded preview persistence, atomic plan/manual CRUD and conversion, unknown-weight protection, canonical project-UID collision safety and demo guards | Passed; e1f712f, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041070343) |
| 69 | Export | One WAL snapshot, original-price whitelist, recorded overrides, explicit units/null/precision, unrelated-project exclusion, mode/demo guards and actual browser attachment | Passed; 596ea18, [Release Validation](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/42041074594) |
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

## Combined integration rehearsal

A separate `resume/completion-integration-rehearsal` branch combines candidates
without merging protected main. Mechanical overlaps retain every scoped browser
workflow, additive migration and logbook entry. Integration fixes are sent back to
their owning PRs. Full Linux validation is pending; local Docker is unavailable.

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

## Production and external gates

The [read-only baseline](completion-program-production-2026-11-05.md) passed SQL
integrity, all five readiness checks, four loaded printer identities, preservation
of both canceled/null-cost SV07 rows and 316 permanent object hashes. There are
1,047 jobs and 435 archived references. No production changes were made.

| Operation | Status | Required next condition |
| --- | --- | --- |
| Required main reviews | Blocked | An eligible non-author reviewer must be available; current collaborator inventory contains only the author |
| Main integration / release / production deployment | Not performed | Required approvals, per-merge validation, release publication, fresh verified backup and safe deployment window |
| Public demo exposure | Not performed | Explicit approval of a concrete endpoint and security configuration, followed by target-specific TLS/egress/capacity verification |
| Physical Pi fresh installation / real power failure / native toolbar zoom / assistive-technology certification | Not tested here | Separate physical/environment validation; automated viewport/CSS zoom and axe results do not establish these |
| Utilization / profit / ROI without defined availability or revenue data | Not implemented by inference | Approved semantics and valid source data; cost alone is insufficient |

No image digest for a new production version is claimed. Existing v0.7.3 and
earlier rollback evidence remains intact. A passing candidate does not replace
the repository's eligible approval requirement.

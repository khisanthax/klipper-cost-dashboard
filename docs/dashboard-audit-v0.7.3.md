# v0.7.3 dashboard acceptance audit

Date: 2026-11-02. Candidate: [PR 59](https://github.com/khisanthax/klipper-cost-dashboard/pull/59), branch `codex/dashboard-ux-v073`. **Implemented and tested; not merged, released or deployed.** Production remains v0.7.2. Root [LOGBOOK](../LOGBOOK.md) is the deployment handoff record.

## Method and safety

Playwright used the actual application served by Waitress, temporary SQLite databases, six simulated Moonraker states and 2,015 synthetic completed jobs. All mutating browser operations, pricing changes, imports, deletes and backup tests used disposable data. The suite also supports smaller histories; CI includes a 500-job fixture. HTTP fixtures simulate firmware responses, not printer motion or heating.

Production was independently checked read-only: v0.7.2 revision `9deb6fd277a7f93c66f4698e76d5dea71d460b8c`, pinned image index `sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8`, readiness true, 1,036 jobs and four printers. Read-only Settings/Diagnostics browser smoke passed for Ender5P, MercuryTwo, SV07 and SV08. MercuryTwo uses port **7129**, not 7127. No production writes, client changes, credential rotation or service restarts were performed. Existing v0.7.1 rollback artifacts remain retained.

## Confirmed findings and repairs

| Severity | Reproduction and impact | Root cause | Completed repair / regression |
| --- | --- | --- | --- |
| High | Delete an assigned hourly or filament profile; readiness breaks or future pricing silently changes. | Deletion ignored active assignments; numeric UIDs could be confused with row IDs. | Reject assigned deletion until replacement/base selection; transactional assignment/deletion and canonical UID matching. Verify rejection, readiness, unchanged history and numeric UID isolation. |
| High | Retained SQL says printing after current Moonraker reports cancelled/disconnected. Dashboard implies stale data is current. | Event state lacked current observation and identity verification. | Cached, bounded read-only telemetry; loaded KCD identity validation; stale/mismatch warnings. Never finalize or clear stored jobs from observations. Six-state browser fixtures and SQL-preservation assertions. |
| High | Many uncached thumbnail requests to an unavailable printer occupy workers. | Concurrent source recovery and repeated endpoint failures were unbounded. | Two server recovery slots, two browser recovery slots, endpoint failure backoff and metadata-only HEAD. Deferred recovery is explicit; health stays responsive during blocked sources. Archived objects bypass recovery. |
| Medium | History filters make another printer's Today total zero; Reports charts empty. | Card input reused filtered rows; chart aggregates omitted. | Independent today/latest-per-printer query and populated day/printer summaries with date regressions. |
| Medium | Refresh loses selected rows; bulk errors disappear. | No deliberate selection lifecycle; controller overwrites history error. | Tab/session, exact-results-page selection; clear on navigation/filter/pagination/action, preserve on reload/sort; Clear Selection and preserved errors. Exact-UID bulk tests retained. |
| Medium | Zero recalculation override vanishes after preview; malformed old conversion crashes mass display. | Truthiness replaced zero; invalid legacy measurements propagated exceptions. | Explicit None checks and unknown-weight handling preserving saved prices. Zero browser previews and invalid-record regressions. |
| Medium | Projects reread the entire profile catalog per job and cannot conveniently select a saved filament profile. | Per-row catalog lookup and separate defaults form. | One catalog read per page; explicit selected-profile snapshot for manual/planned defaults and tracked-job preview. Historical factors and costs remain authoritative. |
| Medium | Names containing apostrophes break confirmation dialogs. | Dynamic names embedded in JavaScript strings without safe serialization. | JSON-safe confirmation strings; quoted project and profile CRUD browser fixtures. Project deletion states which attached data is removed. |
| Medium | Unlabelled controls, low contrast, unfocusable scrolling and mobile overflow. | Shared form markup, heading structure and wrapping gaps. | Associated labels, landmarks, skip link, semantic headings, visible focus, stronger contrast, keyboard-scrollable tables and wrapping headers. Actual Linux mobile overflow repaired rather than hidden. |
| Medium | Compatibility Moonraker CSV import raises a keyword-argument error. | Caller passed unsupported `history_source`. | Remove unsupported keyword; autospecced regression protects call signature. |

## Workflow acceptance matrix

Passed means automated behavior exercised against the real candidate application or the named integration fixture; it does not mean deployed.

| Workflow | Result | Evidence / checks |
| --- | --- | --- |
| Dashboard and printer cards | Passed | Six current states; current filename, weight and estimate; pause exclusion; stale retained state; loaded identity mismatch and network failure. SQL live JSON unchanged. |
| History | Passed | Visible labelled selection, select-all/count, reload/sort persistence, clear/filter/page reset, 100-row page, keyboard horizontal scroll; existing Complete/Recalculate/Delete exact-UID suite. Zero stored prices preserved. |
| Reports | Passed | Saved-cost summaries, known mass plus unknown count, date/printer chart aggregates, responsive rendering. |
| Projects/detail/pricing | Passed | Create/edit/delete, quoted names, assign/remove tracked job, manual create/edit/delete and signed adjustment, planned upload/edit/convert/delete, clear defaults, apply/clear project snapshots without rewriting jobs. |
| Recalculate Center | Passed | Profile IDs/zero rate, explicit overrides, before/after preview, SQL fields and costs after apply, repeated unchanged result, selection/profile-change stale rejection, original restoration and cross-view totals. |
| Printer Settings/Diagnostics | Passed | Disposable profile assignments and readiness, accessible controls; production GET-only four-identity smoke. Existing installer integration suites remain required. |
| Filament and hourly profiles | Passed | Spool creation across three materials/sizes, edit/delete quoted names, assigned deletion rejection, stable original job data. |
| Pause / Display Settings | Passed | Save and verify SQL pause policy; save table visibility and verify actual rendered header state. |
| System Events | Passed | Rendering/accessibility plus existing transactional recalculation before/after audit tests. |
| Backup/admin and thumbnails | Passed | Real archive creation, integrity/foreign keys; existing archive backup/restore and source deletion/move/restart/cache tests. Actual Docker v0.7.2-to-candidate upgrade preserves job/printer/assets/references/settings digests and serves archived images. |
| Physical firmware actions | Not tested | Deliberately excluded: print, home, heat, move, pause/cancel commands against real printers. |
| Other browser engines / screen reader / long soak | Not tested | Chromium automation is not certification of these environments. |

## Financial verification

All new profile inputs retain explicit units. Existing per-meter numbers are not reinterpreted. Recorded per-job mass/factor wins over a fallback; missing information is shown as unknown, not zero. The [workflow guide](dashboard-usage.md) describes saved historical, project-adjusted and projected costs.

Independent geometry uses `grams/m = density(g/cm³) × π × (diameter(mm)/2)²`. For 100 m, zero hourly cost:

| Material | Spool price / net weight | Density / diameter | Independently expected material cost |
| --- | --- | --- | --- |
| PETG | $15 / 1,000 g | 1.25 / 1.75 mm | $4.509903516383785 |
| PLA | $9 / 500 g | 1.24 / 1.75 mm | $5.3685891459032575 |
| ABS | $13.50 / 250 g | 1.04 / 2.85 mm | $35.82669120561147 |

Actual synthetic completed-event SQL values match these independent expectations. The RaRa fixture has 15 jobs, **907.294 m × 3 g/m = 2,721.882 g**. At $15/kg and $0/hour, raw cost is **$40.82823**, displayed **$40.83** in the project/recalculation/history workflow. A changed $15.02/kg preview reconciles displayed cents to $40.88. Project-only snapshots leave original SQL jobs exactly unchanged; global recalculation retains recoverable originals. No real RaRa records were edited.

## Responsive, accessibility and performance evidence

The strict audit covers 11 routes at 1920×1080, 1440×1000, 900×900, 768×1024, 390×844 and 1440×1000 with CSS 200% zoom: **66 page/layout checks**, zero unlabelled controls and zero page-level overflow. Desktop/mobile axe-core 4.14.0 checks across those routes report zero violations. Incomplete axe checks still require human assessment. Keyboard tests cover skip navigation, table scrolling, focus and modal Escape. CSS zoom is not native browser-toolbar zoom; viewport simulation is not physical mobile hardware testing.

The initial 22-page observational baseline found 162 unlabelled-control occurrences and a slowest route of 5.267 s locally. One complete CI run after repairs measured a slowest route of 0.408 s. These are different environments, not a controlled speedup benchmark. Removed synchronous remote live-thumbnail HTML lookup, bounded recovery, cached telemetry with four workers and a five-second TTL, and one Projects catalog read address demonstrated bottlenecks. Ten-second live polling updates elements without reloading forms and pauses when hidden.

One local post-workflow Chromium snapshot used 42,427,712 bytes of JS heap after many navigations. This is an instantaneous observation, not a memory-leak or long-running capacity guarantee. Performance JSON records request failures and browser errors. Lazy image requests aborted by navigation are expected; genuine missing images render status text. Strict workflows assert no page JavaScript exceptions.

## Reproduction and retained evidence

```sh
python -m unittest discover -s tests
python tools/validate_sql_only.py
python tools/browser_dashboard_audit.py --axe
python tools/browser_dashboard_audit.py --baseline --history-jobs 500
python tools/browser_pricing_regression.py
```

The established [Release Validation workflow](../.github/workflows/release-validation.yml) additionally runs History, archived-thumbnail and installer browser/terminal suites, disposable SSH/native systemd acceptance, compatibility modes, Docker build/HTTP smoke and `tools/dashboard_docker_upgrade.py`. Docker is executed on the Linux CI runner, not claimed as locally tested on Windows.

[Release Validation 41479235682](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/41479235682) passed all **226 Linux tests** and required integration/browser/image checks at `e9a8752510b57f097b719d2ee4236bf8ad58f685`. The final PR validation includes expanded manual/planned/display CRUD and the additional 500-job run. See PR checks and the logbook for its final result. Local expanded workflow also passes. CI artifacts retain screenshots, traces, page/axe/performance JSON and redacted server logs; failure screenshots/traces are captured automatically. Artifact retention follows GitHub policy; source harnesses make runs reproducible.

## Release status and remaining work

PR 59 is the release candidate. Required approving review is external and must not be fabricated or bypassed. No v0.7.3 tag, published image or production deployment is claimed. After review/merge, use the established validation/tag/multi-architecture digest/backup/safe deployment process and update the root logbook with independently verified evidence.

Production SV07's retained tracking disagrees with current cancelled telemetry. The candidate flags the disagreement; resolving that real historical event requires an intentional operator decision, not an audit-side automatic completion. Loaded macro identity validation cannot attest physical hardware when firmware does not expose an identity. Other browsers, screen readers, native toolbar zoom, physical phones and long-duration soak remain explicit validation limits. Full recompute modes and unrelated roadmap features remain deferred.

# Production audit and repair — November 2026

## Scope and evidence

Reviewed current `main` (ef7d6b8), installer history, runtime/configuration,
identity/lifecycle, SQL schema and transactions, event APIs, pricing, history,
reports, projects, backups, thumbnails, deployment and release validation. No
open GitHub issues or PRs existed at audit start. The original local checkout's
unfinished edits were preserved; implementation uses a separate worktree.

Observed production on 2026-11-01: vaultwarden, Docker
`print-cost-dashboard`, image v0.5.1, port 6060 → 5000, bind mount
`/home/khisanthax/klipper-cost-dashboard/data` → `/app/data`, SQL storage.
SQLite integrity and all five readiness checks passed. Initial counts: Ender5P
440, SV07 240, SV08 353, MercuryTwo 1 (1,034 total). A consistent private backup
was created before changes. At that baseline, no historical jobs were reassigned or recalculated.

## Findings and repairs

| Severity | Finding, reproduction and impact | Root cause and repair |
| --- | --- | --- |
| High | MercuryTwo's stored `:7127` URL served Ender3V3SE. Its loaded KCD identity, file root and running Klipper process identify MercuryTwo at `:7129`. Wrong thumbnails/history enrichment could affect costs. | Reachability did not validate instance identity. Correct the URL with explicit relocation; compare Moonraker config roots from both installer and Master. |
| High | Installing a new SQL printer inserted a row but omitted pricing. Readiness failed and Settings put it in history-only. | Registration and settings writes were separate and initialized `{}`. Transactional registration initializes missing pricing and pause defaults, retains existing prices/profiles, checks readiness and rolls back on failure. |
| High | Host installer defaulted to CSV while Docker ran SQL. Registry listed only MercuryTwo despite four SQL identities. | Mode selected independently; state paths relative to working directory. Resolve installer data path against checkout, detect matching Docker mount/mode, reject explicit conflicts and a second source Master. Recover identity inventory from SQL without reading legacy installer files. |
| High | API credential was tracked in `data/secret.json`, exposed by prompts/settings, and stored in scripts with world-readable permissions. | Remove tracked credential; generate private credentials on first setup; hidden prompts, no secret defaults/diffs, atomic private writes/backups, bounded overlap rotation and fixed printer-side self-check. Never reproduce the prior value. Historical Git objects still contain the revoked credential; rotation is essential. |
| High | SQL live state vanished on process restart. Cancellation removed state before writing history; duplicate cancellation attempted a forbidden CSV read. | Persist SQL live jobs in `user_settings`, serialize state transitions, preserve state on failed finalization, use SQL for duplicate cancellation lookup. Use a single process with threads. |
| Medium | Remote update referenced undefined `moonraker_url` and `auto_mode` after writing files; remote macro code unpacked a list incorrectly. | Replace divergent install/update paths with one previewable repair workflow, including SSH staging, credential-on-stdin transfer, atomic files and explicit incomplete-install diagnostics. |
| Medium | Installer scanned unused `.cfg` alternatives and could patch both manual and automatic starts. Existing unmarked completion hooks were not recognized. | Traverse `printer.cfg` include graph, support nested/wildcard includes, report cycles/missing includes, resolve effective definitions in Klipper override order and reject inactive targets, detect existing/delegated hooks, generate a diff and require explicit apply. |
| Medium | Discovery reused only saved paths; hard-coded three Moonraker ports and treated localhost/bridge/LAN aliases as separate printers. | Bounded directory candidates plus running Klipper/Moonraker process arguments; associate sockets/config roots and configured ports; prefer LAN endpoints; deduplicate only with machine MACs, instance ID and config root. Unknown ports remain unknown. |
| Medium | Completion retries could produce multiple SQL rows. HTTP errors were treated as successful shell execution. | Stable printer-scoped job UID from Moonraker history ID or optional event UUID, duplicate guard, bounded completion retries and curl failure handling. Secrets are passed via header input rather than process arguments. |
| Medium | Docker/source services used Flask's development server. Image publication could finish despite failed validation and only published the host architecture. | Waitress, one process/four threads; pristine SQL initialization at first server start; validation gates publication; amd64, arm64 and arm/v7 image builds. |
| Medium | Corrupt credential file could be silently replaced, disconnecting clients. Build context could include private runtime files. | Fail closed on corrupt credentials; ignore runtime data/backups in Docker context; pin dependency closure and audit it. |

There is no evidence that an upgrade deleted installer registrations. The
current host checkout predates the running release and its state file contains
only the recent local installation. Relative paths, separate installer and
runtime authorities, and SQL's deliberate legacy-file exclusion explain why
the old registry cannot establish production truth. Recovery derives current
identities from SQL and locations from active Klipper/Moonraker configuration.

MercuryTwo includes `macro.cfg` → `print_start_manual.cfg`. The alternate auto
file is inactive. Keep this custom selection; do not replace motion or
temperature logic. Ender5P remains Mercury One's identity on its dedicated Pi.

## Validation and limits

The baseline suite had 156 tests (one platform-specific skip on Windows).
Added registration/discovery/macro/rotation/state regressions cover the
specified installation failures; existing suites cover projects, pricing,
pause accounting, cancellation, SQL mutation failures, backups, history,
reports, imports, recalculation, health and filesystem isolation. Dependency
audit resolves no known advisories for the pinned release closure.

Live verification is read-only except documented backed-up configuration,
credential and deployment repairs. Physical printing, temperature/motion
commands and forced Klipper restarts are not needed to verify these changes.
Synthetic jobs belong only in the isolated smoke-test database.

CSV/dual remain compatibility modes. They cannot provide a cross-filesystem /
SQLite transaction; a reported file failure requires retry. SQL-only remains
canonical and never falls back to legacy files. Delivery retries cover bounded
transient failures, not a durable offline client queue; failed shell commands
remain visible and require operator replay. A single Master process is required.
Macro call-graph checks are conservative, not a complete Jinja interpreter.
Existing custom include or wrapper ambiguities require an explicit target.

Deployment verification, image identifiers and final live counts are recorded
in the release/deployment section after completing the rollout.

## Expanded financial, selection and thumbnail findings

| Severity | Reproduction / root cause | Repair and evidence |
| --- | --- | --- |
| Critical | Select 15 jobs and new profiles without submitting GET filters. The POST action form carried stale hidden plan copies, while success counted matched updates. Production profile IDs were valid, but jobs retained 1/hour and .25/m. | Actual controls now belong to POST; reviewed hashes bind jobs/plan/profile prices; SQL reload validation and atomic before/after audit; changed vs unchanged counts; first original costs retained. Chromium verifies all 15 fields/costs, one untouched job, project totals, repeated apply and recovery. |
| High | SQL history reads treated zero as missing, allowing current defaults to replace legitimate free costs in display. | Only missing values may be filled. Complete saved cost components bypass current profile computation; explicit zero/signed values survive reads and writes. |
| High | Project kg overrides affected manual/planned work but not tracked costs; no explicit tracked overlay existed. | Project-only preview/apply snapshots preserve original jobs, show length/mass/factor/source and signed components, reconcile cents, and distinguish historical, adjusted and hypothetical prices. Acceptance example yields 2.50506 kg, $37.5759 material, $180.21 total. |
| High | Pricing inputs used incompatible units; operators manually converted spool prices to per-meter rates. | Shared explicit-unit normalization, spool/net-weight input, material density/input-diameter conversion or verified g/m, per-job precedence and missing-conversion errors. Existing numeric prices keep their units. |
| High | Tiny native History controls lacked names/counts; generic persisted column resizing could collapse the control column. | Explicit high-contrast 20px controls, 44px labels, fixed sticky selection width, partial/select-all counts, stable UID-only operations. Chromium covers desktop, narrow/mobile, 200% zoom, scrolling and exact Complete/Recalculate/Delete targets. |
| High | Temporary filename/URL-dependent images disappeared after expiry, source deletion or relocation. | Content-addressed persistent objects, stable SQL job references, atomic start-asset attachment on completion/cancel, conservative recovery/backfill, coordinated backup/restore and explicit unreferenced pruning. Browser test deletes source before completion, relocates URL, restarts and displays all four views. |
| Medium | Each historical thumbnail could perform remote requests during project HTML rendering, multiplying offline-printer delay by job count. | Lazy job-scoped retrieval and short negative caching keep network work outside page rendering. |
| Medium | Dashboard mutation forms accepted cross-origin browser submissions; CDN failure could remove expected styling. | Same-origin checks before mutations; integrity-verified Bootstrap 5.3.8 served locally. The dashboard remains within its documented trusted-LAN administrative boundary. |

## Live configuration and credential repairs completed

Verified all four printers from their own Moonraker context. MercuryTwo uses
`:7129`; Ender5P retains its dedicated Pi address and historical identity. SQL
operational metadata was recovered for all four clients. The exposed key was
rotated with bounded overlap, all four self-checks passed, and the previous key
was revoked. No key values appear in this report.

Backed up and patched only active custom pause/resume/cancel definitions and
missing managed helpers. Preserved start/end hooks and all motion/temperature
logic. Both Mercuries use `print_start_manual.cfg`; the alternate automatic
configuration remains inactive. Sovols use their effective custom macros.
Loaded gcode was verified after idle-only configuration reloads; all four were
ready. Fixed CANCEL insertion to target the actual gcode body after
`CANCEL_PRINT_BASE`, never the `rename_existing` option.

The completed rollout is recorded below.

## Completed release and production verification

Published and deployed v0.7.1 on 2026-11-01. The active private Compose override
pins the multi-architecture image to:

`ghcr.io/khisanthax/klipper-cost-dashboard:v0.7.1@sha256:c0871372d0b8fb33a43d30094e566d960d4b3d64e10a5537c7d122caadf53647`

The source checkout was updated through merge `66b01f1`, retaining local ignore
rules and the pre-existing legacy recalculation log. SQL readiness passed all
five checks with schema `0008_pricing_provenance`; integrity and foreign-key
checks passed. Before pricing changes, a digest of every pre-existing job field
proved the additive upgrade preserved all 1,034 records. Printer IDs, history
membership and existing defaults were retained: Ender5P 440, SV07 240, SV08 353,
MercuryTwo 1.

The real browser then selected all 15 RaRa Masks jobs, enabled the verified
RaRa Hours and RaRa Mats profiles, reviewed the reconciled preview and applied
it. SQL reload verification confirmed every job stores 0/hour, per_kg at 15,
3 g/m and the intended profile IDs. Requested/changed/validated counts were all
15, missing count zero. The resulting 907.294 m is 2,721.882 g; material cost is
40.82823 and time is zero. Project display is $0.00 time + $40.83 material +
$0.00 adjustment = **$40.83**, versus the original $382.11. This is the current
15-job case; the separate 835.02 m / $180.21 acceptance example also passes.

All 1,019 unselected history rows remained byte-for-byte equivalent in their
original fields. Reconstructing the pre-change history from the 15 retained
original snapshots and current unselected rows reproduced the complete original
history digest. Both original prices and the verified before/after SQL audit
are recoverable. No global printer defaults or active-job pricing were changed.

Thumbnail recovery archived 422 existing jobs as 311 unique objects: 44 proven
cache recoveries and 378 unchanged-source captures. Remaining results were 570
missing sources, 16 sources modified after their jobs and 26 jobs without enough
timestamp evidence. Those were reported and not guessed. An isolated restore of
the production backup verified all 1,034 jobs, all 422 references, every image's
size/SHA-256 and a restored HTTP image response. Representative private backups:
`kcd_backup_20261101_151201.tar.gz` before upgrade/pricing,
`kcd_backup_20261101_152247.tar.gz` after archive recovery, and
`kcd_backup_20261101_153412.tar.gz` after the verified pricing operation.

SV07 began a real print during verification. Its credential matched the current
Master and its accepted live event retained a start thumbnail. The dashboard-only
v0.7.1 update used a graceful stop and completed in 0.62 seconds. The SQL live
state was identical across the update and Moonraker still reported printing.
Klipper was not restarted for this dashboard update. The other three clients'
fixed diagnostics also verified their credentials. MercuryTwo's inactive auto
configuration received backed-up description corrections only; its include
selection and motion/temperature logic were unchanged.

Final validation: 203 tests pass on Linux/Python 3.11; Windows has one
platform-specific skip. All three Chromium suites and the Docker HTTP smoke
pass in release CI. Coverage includes profile application/recovery, fractional
cent reconciliation, safe profile identity collisions, graceful SIGTERM with a
live SQL job, archive deletion/backup/restore, viewport/zoom/scroll selection and
exact bulk-action targets. The live browser confirmed the 15-job SQL result and
project components. Production History controls were also checked on desktop
and mobile without applying other bulk mutations. All principal routes returned
HTTP 200, and a cross-origin mutation returned 403 before processing. The
production backup and original historical identities remain available for
recovery.

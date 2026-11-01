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
was created before changes. No historical jobs were reassigned or recalculated.

## Findings and repairs

| Severity | Finding, reproduction and impact | Root cause and repair |
| --- | --- | --- |
| High | MercuryTwo's stored `:7127` URL served Ender3V3SE. Its loaded KCD identity, file root and running Klipper process identify MercuryTwo at `:7129`. Wrong thumbnails/history enrichment could affect costs. | Reachability did not validate instance identity. Correct the URL with explicit relocation; compare Moonraker config roots from both installer and Master. |
| High | Installing a new SQL printer inserted a row but omitted pricing. Readiness failed and Settings put it in history-only. | Registration and settings writes were separate and initialized `{}`. Transactional registration initializes missing pricing and pause defaults, retains existing prices/profiles, checks readiness and rolls back on failure. |
| High | Host installer defaulted to CSV while Docker ran SQL. Registry listed only MercuryTwo despite four SQL identities. | Mode selected independently; state paths relative to working directory. Resolve installer data path against checkout, detect matching Docker mount/mode, reject explicit conflicts and a second source Master. Recover identity inventory from SQL without reading legacy installer files. |
| High | API credential was tracked in `data/secret.json`, exposed by prompts/settings, and stored in scripts with world-readable permissions. | Remove tracked credential; generate private credentials on first setup; hidden prompts, no secret defaults/diffs, atomic private writes/backups, bounded overlap rotation and fixed printer-side self-check. Never reproduce the prior value. Historical Git objects still contain the revoked credential; rotation is essential. |
| High | SQL live state vanished on process restart. Cancellation removed state before writing history; duplicate cancellation attempted a forbidden CSV read. | Persist SQL live jobs in `user_settings`, serialize state transitions, preserve state on failed finalization, use SQL for duplicate cancellation lookup. Use a single process with threads. |
| Medium | Remote update referenced undefined `moonraker_url` and `auto_mode` after writing files; remote macro code unpacked a list incorrectly. | Replace divergent install/update paths with one previewable repair workflow, including SSH staging, credential-on-stdin transfer, atomic files and explicit incomplete-install diagnostics. |
| Medium | Installer scanned unused `.cfg` alternatives and could patch both manual and automatic starts. Existing unmarked completion hooks were not recognized. | Traverse `printer.cfg` include graph, support nested/wildcard includes, report cycles/missing includes, reject duplicate definitions and inactive targets, detect existing/delegated hooks, generate a diff and require explicit apply. |
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

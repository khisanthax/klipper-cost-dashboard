# Installer acceptance audit — November 2026

Baseline: [v0.7.1 production audit](production-audit-2026-11.md) and
[installation recovery contract](installation-recovery.md). Scope: actual installer
menus, registration, repair, upgrade, retirement and browser visibility. Test
changes are in PR [57](https://github.com/khisanthax/klipper-cost-dashboard/pull/57).
All failure injection uses disposable data, processes, services and containers.

## Findings and repairs

| Severity | Reproduction / impact | Root cause and repair |
| --- | --- | --- |
| High | Run `install.py discover` with a nonexistent data directory; discovery created Master state and a credential. | Eager interactive imports initialized runtime state. Dispatch CLI before those imports; subprocess regression asserts the directory remains absent. |
| High | Update a selected printer; generic installation lost the selected identity. | Update wrappers discarded the name. Pass it through, reject a discovered configuration belonging to another identity, preserve historical records. |
| High | Fresh source Master under sudo could not read its private credential/database; environment key and stored service key could disagree. | Root created files for a non-root service; sudo environment is not durable service configuration. Persist the intended credential privately, reject conflicting authorities, and assign only fresh known runtime files to the intended service owner. Existing root-owned printer data requires deliberate ownership repair. |
| High | Failed or unhealthy source service was reported installed. Forced update could leave the old process running. | Success ignored final health; `enable --now` did not restart an already active process. Check daemon-reload/enable/restart results and bounded service/listener/HTTP health, then report failure with retained recovery state. |
| High | Master uninstall cleared preferences, offered deletion of repository Docker files, and left the actual configured service running. | Hard-coded artifacts and no service lifecycle operation. Remove only the saved source service belonging to this checkout, back up its unit privately, stop/disable it, retain all data/keys/client files/source. Docker ownership blocks source uninstall. Reinstall is exercised in the native systemd fixture. |
| High | Custom printer configuration directory containing spaces produced invalid shell commands. | Unquoted generated paths and a legacy path rewrite that assumed no spaces. Quote each script path, preserve trailing macro parameters, and test actual script execution. |
| High | Root remote repair wrote owner-only scripts that the printer user could not run. | Atomic replacement inherited the installer owner. Set managed files to the selected config directory owner; native combined-install fixture executes the private script as the real service user. |
| High | A managed script symlink could write outside the selected configuration directory. | No containment check before atomic replacement. Reject escaping managed targets before registration; shared read-only include traversal remains supported. |
| Medium | Preview reported unchanged files and empty diffs. | Plans included all managed files regardless of content/mode. Skip identical files with correct permissions; repeated repair has zero changes. |
| Medium | Configuration selection could not return to the previous menu. | Numeric-only prompt had no cancel path. Accept `b`, return immediately and test bounded EOF/interruption. |
| Medium | Invalid Master ports silently fell back to 5000; invalid service names failed after preferences were saved. | Validate URL, port range and service name before persisting settings or installing a service. Actual terminal cases reject nonnumeric/out-of-range ports and path-like service names. |
| Medium | CSV/dual clients installed successfully but were absent from installer update/repair menus; relocation could leave two locations. | Unified registration persisted Master metadata but omitted the caller's legacy operational registry. Persist non-SQL caller metadata after validated success; replace the previous location for the same identity. SQL-only never creates an installer JSON authority. |
| Medium | A standalone client host offered an unrelated locally generated API key. | Client prompt used the local Master secret unconditionally. Prefer explicit environment credentials, otherwise reuse an installed script credential only for its matching Master; remote callers without a known Master credential must enter one. No clear-text defaults. |
| Medium | Remote staging generated an unrelated secret file. | Payload credential was supplied after importing runtime-dependent workflow code. Supply it in process memory before imports; transport over stdin and remove private staging. |
| Medium | Fresh CSV/dual Settings displayed hard-coded or example printer cards. | Legacy bootstrap registered sample settings. Initialize missing pricing settings empty; existing settings remain untouched and registration supplies required defaults. |
| Medium | Settings controls had visible text labels without accessible associations, and headers overflowed narrow cards. | Associate printer-specific controls with labels, name history limits, allow header wrapping; browser tests verify visible, enabled, focusable pricing and endpoint fields in all four layouts. |

No history merges, original-price rewrites, credential rotations or production
macro modifications are part of this audit. A file failure after successful
Master registration deliberately retains registration and private per-file
backups; rerun the same repair. This is recoverable convergence, not a distributed
transaction or a claim that partial registration was rolled back.

## Acceptance matrix

`Passed` identifies the test boundary explicitly. It does not imply a real print
was performed by an emulated Moonraker endpoint. `Not Tested` is a coverage limit.
The release-validation workflow must be green for the final release commit.

The code acceptance run [41365488942](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/41365488942)
passed 211 unit tests, all six browser/backend suites, native systemd recovery,
OpenSSH, Docker authority and final release-image smoke checks. Final-commit
validation is linked from PR 57. Repository rules require one approving review
and last-push approval; publication/deployment awaits that review rather than
using the administrator bypass.

| Workflow | Result | Evidence / boundary |
| --- | --- | --- |
| Fresh SQL source Master, manual input, intended credential, readiness | Passed | Actual sudo installer and native systemd service on disposable Ubuntu CI VM; authenticated HTTP verification. |
| Saved automatic Master settings, existing-service skip, forced upgrade | Passed | Native service fixture; verifies HTTP readiness after each operation. |
| Combined Master and local client | Passed | Native systemd fixture plus HTTP Moonraker protocol fixture; actual private script diagnostic runs as service user. |
| Docker SQL Master / host source consistency | Passed | Actual image, bind mount and interactive host installer; detects SQL, blocks a second Master, creates no legacy state files. |
| Fresh first and multiple client registrations | Passed | Actual CLI and live KCD HTTP server, independent Moonraker fixture ports; SQL rows, defaults, readiness, visible cards. |
| Manual local client and automatic saved Moonraker reuse | Passed | Actual Linux stdin-driven installer prompts, twice; config directory contains spaces. |
| Remote install, preview, repeat and authentication failure | Passed | Actual OpenSSH container, strict disposable host keys, actual remote installer menu and transport; target has no Flask. |
| Same-host multiple Moonraker instances / wrong instance selection | Passed | Two HTTP protocol fixtures, real registration; wrong config-root/port rejected. |
| Duplicate/cloned identity / relocation | Passed | Live HTTP clone rejection and unchanged client files; unit tests preserve job identity across explicit relocation. |
| Existing SQL registration without installer JSON | Passed | SQL recovery regression plus actual registered-client menu; no legacy state authority. |
| Repeated client repair, missing file, macro repeat | Passed | Actual CLI, zero-change repeated preview, missing script recreation, byte-identical repeated macro patch. |
| Local retirement and reactivation | Passed | Actual SQL interactive menu plus explicit CLI reactivation; historical sentinel cost remains unchanged. |
| Master uninstall and reinstall | Passed | Actual disposable native service; private unit backup, data/key/source retained, prior client still registered. |
| CSV and dual compatibility | Passed | Separate fresh servers and real CLI/menu/browser suites; valid pricing, stable registrations and existing historical SQL sentinel retained. SQL-only retirement menu variant is tested separately. |
| Active manual vs inactive automatic macros / shared includes / overrides | Passed | Active-chain fixture and macro-plan tests; inactive patch refused, custom commands retained. Production uses inspection only. |
| Wrong credentials, closed Master/Moonraker port | Passed | Actual HTTP/CLI rejection; no credential in output, no false success. |
| Expired credential overlap | Passed | Authentication unit test with expired and unexpired rotation metadata; no live rotation. |
| Invalid pricing / identity and database registration failure | Passed | Transaction rollback unit tests and actual HTTP registration with rejecting SQLite trigger; retry after removing trigger. |
| Permission failure / interrupted client writes | Passed | Injected third-write PermissionError after two real writes, then ordinary CLI repair; EOF before apply also bounded. This is not a physical power-loss test. |
| Failed native service verification | Passed | Targeted service-health regression returns failure; fresh/restart/reinstall native success paths also tested. Package-registry outage and kernel/disk failure are not simulated. |
| Responsive pricing / registration / diagnostics | Passed | Real Chromium/KCD at 1440×1000, 768×1000, 390×844 and 200% CSS zoom; named/focusable controls, screenshots, diagnostic failure/recovery alerts, no browser JS errors. |
| Existing History selection / pricing previews / retained thumbnails | Passed | Existing real-browser suites remain mandatory CI gates, alongside SQL-only isolation, backup and restore regressions. |
| Production Settings / Diagnostics | Passed | GET-only Playwright against vaultwarden; all four identities, browser network layer rejects mutations. |
| Production Mercury 1 / Mercury 2 repair | Passed | Read-only actual local/remote dry-runs, `applied: false`, no proposed file changes. |
| Production SV07 / SV08 | Passed | Read-only Moonraker service and config-root reference plus browser identity presence. SSH repair/dry-run not verified for these hosts. |
| Fresh physical Raspberry Pi OS installation / live Klipper motion and thermal macros | Not Tested | Linux SSH fixture emulates the protocol/filesystem boundary. Production Pi read-only repair works; no hardware was reinstalled or reloaded. |
| Firefox, Safari, screen-reader audit and native browser-chrome 200% zoom | Not Tested | Chromium tests use CSS zoom. Label/focus checks are not a comprehensive assistive-technology certification. |
| Physical power loss, full disk, failed OS/package upgrade, arbitrary customized source service | Not Tested | Recoverability is validated with deterministic process/file/DB failures, not these environmental failures. |

## Production reference snapshot

Read-only checks on 2026-11-01 (America/New_York):

| Identity | Moonraker endpoint | Config root | Service connected |
| --- | --- | --- | --- |
| Ender5P (Mercury 1) | `192.168.2.130:7125` | `/home/khisanthax/printer_data/config` | Yes |
| MercuryTwo | `192.168.2.55:7129` | `/home/khisanthax/printer_mercurytwo_data/config` | Yes |
| SV07 | `192.168.2.114:7125` | `/home/biqu/printer_data/config` | Yes |
| SV08 | `192.168.2.194:7125` | `/home/biqu/printer_data/config` | Yes |

Port 7127 on vaultwarden belongs to Ender3V3SE, not MercuryTwo. Discovery
identifies active multi-instance directories and leaves the old Ender5P copies
unverified; none are automatically selected. Master is SQL, container
`print-cost-dashboard`, host port 6060, persistent bind `/app/data`. No production
POSTs, failure injection, database edits, client installs, restarts or key changes
were performed to obtain these results.

## Reproduce and inspect evidence

```bash
python -m pip install -r requirements.txt -r requirements-browser.txt
python -m playwright install chromium
python -m unittest discover -s tests
python tools/validate_sql_only.py
python tools/smoke_production.py --installer-acceptance --installer-ssh
python tools/smoke_production.py --installer-acceptance --compat-backend csv
python tools/smoke_production.py --installer-acceptance --compat-backend dual
python tools/installer_docker_acceptance.py
```

Use Linux with Docker and OpenSSH tools for SSH/Docker fixtures. Interactive
hidden-key stdin paths are exercised on Linux; Windows getpass uses the console,
so those paths are skipped on Windows. Native service acceptance is intentionally
restricted to the disposable GitHub Actions VM:
`python tools/installer_systemd_acceptance.py`. Do not defeat that guard on a
production machine. The harness uses actual installer subprocesses and prompts;
it is not a terminal emulator or a mock of input functions.

CI installs Chromium system dependencies, runs all suites before image publication,
and uploads `browser-artifacts/` even on failure. Each backend keeps screenshots,
a Playwright trace and redacted terminal output under `installer/{sql,csv,dual}/`.
Native service and remote-menu transcripts are retained too. Inspect traces with
`python -m playwright show-trace PATH/trace.zip`. The intentionally failing
Moonraker diagnostic is captured as `diagnostic-failure.png`; unexpected browser
assertions also produce `failure.png` and the trace. Artifacts use disposable
credentials; production traces/screenshots remain local and must be treated as
private operational evidence.

Optional production reference (enforces browser GET/HEAD/OPTIONS only):

```bash
python tools/browser_installer_production.py --base-url http://192.168.2.55:6060
```

This command never submits forms and is not called by public CI. Follow
[recovery and rollback](installation-recovery.md) for real deployments. Keep the
running production image pinned during an installer-only checkout update; changes
to runtime templates take effect only through the normal verified image release.

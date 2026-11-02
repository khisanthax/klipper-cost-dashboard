# KCD Engineering LOGBOOK

This is the canonical, human-readable handoff record for completed Codex work on **Klipper Cost Dashboard (KCD)**. Keep it in GitHub so a future ChatGPT session can read the current project state without relying on a prior conversation.

## Working agreement for Codex

Read this root logbook before beginning every KCD task. It is the authoritative
project handoff record. Root `AGENTS.md` repeats this requirement for future agents.

For **every completed substantive task**, append a dated entry to this file in the same PR or commit as the work, or in a follow-up documentation commit before declaring the task complete. If code is awaiting review or deployment, record that status explicitly—do not call it deployed.

Each entry must include:

- **Date / task / scope:** the task goal and affected components.
- **Status:** implemented, tested, merged, released, deployed, blocked, or partially completed (distinguish these).
- **What changed:** concise summary, important files or behavior.
- **Verification:** exact tests run, pass/fail results, CI run links, and scope of production checks. Distinguish simulated tests from live verification.
- **Git references:** commits, PRs, tags, image digest, and relevant documentation or evidence.
- **Production state:** running version if independently verified; data integrity, clients, restarts, migrations, backups, and rollback evidence when relevant.
- **Open items / next action:** remaining failures, non-tested areas, and the single next concrete step. Use `None` only if none remain.

Never include credentials, API keys, personal data, private backup contents, or secret-bearing logs. Link to controlled evidence rather than copying sensitive content. Keep older entries intact; correct errors with a dated correction instead of silently rewriting history. Update this logbook in the repository and provide a link in the final Codex completion message.

## Latest established baseline

### 2026-11-02 — v0.7.2 installer acceptance and deployment

- **Status:** released and deployed, per the release's attached production evidence.
- **Scope:** installer discovery, selected-printer identity, source-service health/ownership, credential reuse, custom paths, compatibility registration, safe uninstall/recovery, responsive Settings, and expanded end-to-end testing.
- **Change:** merged PR [#57](https://github.com/khisanthax/klipper-cost-dashboard/pull/57) into main at commit `9deb6fd277a7f93c66f4698e76d5dea71d460b8c`.
- **Release:** [v0.7.2](https://github.com/khisanthax/klipper-cost-dashboard/releases/tag/v0.7.2). Deployment reported at **2026-11-02 09:27 America/New_York**.
- **Pinned production image digest:** `sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8`.
- **Validation:** release documentation reports 211 unit tests, browser suites, SQL-only checks, OpenSSH, native systemd, Docker and image smokes, plus post-merge and tag validation.
- **Data preserved:** 1,036 jobs, 424 thumbnail references, 312 image objects; SQL readiness/integrity and four printer connections verified in the deployment evidence.
- **Operations:** no printer services restarted; v0.7.1 rollback image, offline archive, backups, and recovery configuration retained.
- **Evidence:** [deployment evidence](https://github.com/khisanthax/klipper-cost-dashboard/releases/download/v0.7.2/v0.7.2-deployment-2026-11-02.md), [installer acceptance](docs/installer-acceptance-2026-11.md), [recovery guide](docs/installation-recovery.md).
- **Open items / next action:** no reported release blocker. Continue normal operating checks and use this logbook for subsequent Codex handoffs. Audit limits include physical Pi fresh install, non-Chromium browsers, comprehensive screen-reader testing, and real power/disk failure injection.

### 2026-11-02 — Persistent logbook-first handoff instructions

- **Status:** documentation implemented and committed to the GitHub branch for PR [#58](https://github.com/khisanthax/klipper-cost-dashboard/pull/58); awaiting review/merge. No application release or deployment is part of this documentation task.
- **Scope / goal:** make future KCD tasks read this root logbook first and publish their completed-work handoff before reporting completion.
- **What changed:** added root `AGENTS.md` with mandatory read/update/commit/push instructions and explicit deployment-verification requirements. Preserved the existing baseline established by main commit `2d29114cded9b8184c938c34856d33788a0dc7ea`. PR 58 also contains the exact [v0.7.2 deployment and rollback report](docs/v0.7.2-deployment-2026-11-02.md), originally committed as `03fec123e6c67c9e5e6b002cb80000d9bb8bbb54`.
- **Verification:** read the GitHub main logbook before editing; `git diff --check` passed; confirmed root instructions, report and logbook are tracked. No application tests rerun for these documentation-only changes; new documentation CI status is available on PR 58 and is not claimed passed in advance.
- **Git references / release:** implementation PR [#57](https://github.com/khisanthax/klipper-cost-dashboard/pull/57), release commit `9deb6fd277a7f93c66f4698e76d5dea71d460b8c`, [v0.7.2 release with attached deployment evidence](https://github.com/khisanthax/klipper-cost-dashboard/releases/tag/v0.7.2). [Post-merge validation 41460903362](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/41460903362) and [tag validation/publication 41461432288](https://github.com/khisanthax/klipper-cost-dashboard/actions/runs/41461432288) passed, including 211 unit tests and the installer/browser/SSH/systemd/Docker suites.
- **Production state / rollback:** no production operations performed for this documentation task. Last independently verified during the preceding deployment on 2026-11-02 at 09:29 America/New_York: vaultwarden running v0.7.2 at index digest `sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8`, revision `9deb6fd277a7f93c66f4698e76d5dea71d460b8c`, SQL HTTP readiness passed and restart count zero. Verified 1,036 unchanged jobs, 424 thumbnail references, 312 object hashes, database integrity/foreign keys and four Moonraker connections. Actual MercuryTwo local and Mercury 1 remote installer dry-runs proposed zero changes. v0.7.1 private override, local rollback image, offline image archive and SQL/object backup are retained; see the deployment report for exact paths, digests and recovery commands.
- **Open items / next action:** obtain normal review and merge PR 58 so the new root agent instructions and detailed report are on main. Existing operational observation: SV07's retained SQL live-job entry says printing while Moonraker reported cancelled at deployment; this release deliberately preserved it, and no reconciliation fix is claimed. SV07/SV08 SSH repair was not verified. Physical Pi fresh installs, non-Chromium browsers, comprehensive screen-reader coverage and real power/disk failures remain untested as recorded in the acceptance matrix.

## New entry template

### YYYY-MM-DD — Task title

- **Status:**
- **Scope / goal:**
- **What changed:**
- **Verification:**
- **Git references / release:**
- **Production state / rollback (if relevant):**
- **Open items / next action:**

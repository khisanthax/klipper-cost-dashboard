# KCD Engineering LOGBOOK

This is the canonical, human-readable handoff record for completed Codex work on **Klipper Cost Dashboard (KCD)**. Keep it in GitHub so a future ChatGPT session can read the current project state without relying on a prior conversation.

## Working agreement for Codex

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

### 2026-11-02 — v0.7.3 dashboard UX and functional audit (in progress)

- **Status:** investigation and implementation in progress on `codex/dashboard-ux-v073`; not merged, released or deployed. Existing local user edits remain untouched in the original checkout.
- **Scope / goal:** exercise every major dashboard workflow using actual Playwright pages, disposable SQL/Moonraker fixtures and realistic history volumes; prioritize explicit spool pricing, project/recalculation correctness, safe selection, accessibility and truthful live state.
- **Findings / work so far:** baseline browser audit covered 11 routes at desktop/mobile with 2,015 synthetic jobs and six simulated printer states. It found 162 unlabelled-control occurrences and an initial dashboard render of 5.267 seconds. Confirmed empty chart aggregates, discarded bulk-action errors, refresh losing History selection, and retained event state presented without current printer verification. Initial repairs add associated labels, tab/session page-scoped selection with Clear Selection, error preservation, populated chart aggregates and a bounded cached read-only telemetry endpoint. Current observations do not finalize or clear retained tracking. Removed remote live-thumbnail lookup from dashboard HTML rendering; saved archive references remain authoritative.
- **Verification so far:** five new targeted Python regressions pass. Baseline browser evidence and traces are in ignored `browser-artifacts/dashboard-audit/`; subsequent full workflow/financial/viewport acceptance and the complete release validation are still required. Initial retest measured a slowest route of 2.156 seconds and reduced unlabelled occurrences to 70 before remaining label fixes; no final accessibility claim yet.
- **Production state:** independently read-only verified v0.7.2, revision `9deb6fd277a7f93c66f4698e76d5dea71d460b8c`, index `sha256:63521ccbffb11963fbd0c2ea975e83e3571c4302089fd74c73043ce34a4378b8`, SQL readiness true, 1,036 jobs/four printers. No production data, printer services, credentials or client files changed for this audit.
- **References / remaining work:** prior deployment/logbook instructions remain in PR 58 awaiting review. Continue pricing/project CRUD and stale-preview workflows, live-state fixtures, missing-conversion cases, six responsive layouts, selection/bulk-scope tests, performance/accessibility checks, full CI, audit/user/roadmap documentation and release readiness. Preserve existing rollback artifacts. Review requirements remain in force; no release/deployment is claimed for v0.7.3.

## New entry template

### YYYY-MM-DD — Task title

- **Status:**
- **Scope / goal:**
- **What changed:**
- **Verification:**
- **Git references / release:**
- **Production state / rollback (if relevant):**
- **Open items / next action:**

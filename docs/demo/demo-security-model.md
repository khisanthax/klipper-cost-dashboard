# Demo application security model

Status: Phase 4 application boundary. The demo is not public-ready; container egress, reverse-proxy trust, monitoring, abuse operations, and deployment hardening remain incomplete.

## Default-deny request boundary

Demo mode classifies every Flask endpoint and HTTP method as `public_safe`, `workspace_read`, `workspace_mutate`, `workspace_lifecycle`, `operator_only`, or `blocked_demo`. An absent endpoint/method or an unapproved multiplexed form action is blocked before route code. Application startup fails when the actual URL map contains an unclassified combination. Normal mode bypasses this policy.

Public surface is limited to static assets, minimal `{status: available}` health, and workspace bootstrap. Workspace reads cover dashboard, reports, projects, recalculate, settings, and workspace event views. Approved form mutations cover history completion/deletion/recalculation; safe projects/manual/planned records; local printer display/pricing changes; profiles, rate profiles, pause settings, mappings, and display columns. Upload, export, diagnostics, printer-client APIs, live-job control, backup, Moonraker URL/test/import, remote thumbnails, and unknown actions are blocked.

## CSRF and origin

The CSRF value is `HMAC-SHA256(raw_session_token, "kcd-demo-csrf-v1")`. It is unique to the opaque browser session, is not stored, survives reset because reset preserves session identity, and expires with that identity. A token from workspace A cannot authorize B. HTML responses receive a hidden `_csrf_token` in every POST form; JSON/fetch clients use `X-CSRF-Token`. Comparison is constant-time.

All mutations validate `Origin`, or `Referer` when Origin is absent. Scheme and host must equal the direct request values. Forwarded headers are ignored unless `KCD_DEMO_TRUST_PROXY=1`. With `KCD_DEMO_EXPECT_HTTPS=1`, missing origin evidence is rejected and HTTPS is required. Local HTTP development may explicitly leave that setting disabled; absent browser-origin headers are then accepted only with a valid CSRF token.

## Abuse and resource controls

Fixed-window rate counters live in the registry SQLite database and update under `BEGIN IMMEDIATE`. Identities are SHA-256 scoped values: internal workspace identity for valid sessions and direct remote address for unresolved traffic. Forwarded addresses require explicit proxy trust. Expired buckets are pruned.

Entity creation uses registry quota slot reservations. The transaction reconciles committed SQL counts with a persistent high-water slot, preventing stale concurrent reads from bypassing a limit. Failed-request slots are released; successful slots are reconciled by the next count or bounded expiry. Seed size is checked before cloning, the contained workspace is measured before mutation, and SQLite `max_page_count` limits database growth on every demo connection. Uploads and exports remain disabled, so other visitor-controlled file growth is unavailable.

## Network and filesystem boundary

Dangerous routes are rejected before their clients or helpers run. Moonraker and thumbnail network primitives also call a central outbound-network guard, making demo mode deny-all even if future route policy regresses. Demo seeds containing an active `moonraker_url` are rejected. No HTTP route exposes installer, updater, restore, registry, cleanup, seed, CLI, shell, subprocess, host-path, file-browser, upload, or arbitrary-download behavior.

All mutable data remains under `WorkspaceContext`. Existing containment, symlink, immutable-seed, reset, cleanup, and connection-teardown controls remain authoritative. CSV export stays blocked until it can be generated and bounded entirely inside the workspace.

## Responses

Demo responses use stable JSON errors for APIs and functional HTML errors for pages. Unexpected errors expose only a random correlation reference and logs omit exception text. Response redaction removes the raw token, token hash, full workspace ID, and workspace/database paths. Redirect error details are normalized.

All demo responses receive `nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`, and a restrictive `Permissions-Policy`. Session-sensitive responses use `Cache-Control: no-store`. HSTS is emitted only with `KCD_DEMO_EXPECT_HTTPS=1`. A broader script/style CSP is deferred because existing templates contain inline scripts and styles.

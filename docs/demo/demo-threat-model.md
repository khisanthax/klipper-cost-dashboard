# Demo threat model

Status: application-layer Phase 4 model; public deployment controls remain out of scope.

## Assets and trust boundaries

Assets are each visitor database and mutable directory, immutable seeds, the shared control registry, normal KCD data, bearer session tokens, host files, credentials, and private/public network reachability. Untrusted inputs include cookies, URL/query/form/JSON data, headers, filenames, methods, concurrent requests, and expired or guessed identities. The application process and operator CLI are trusted; a visitor is not.

## Addressed threats

| Threat | Application control |
| --- | --- |
| Cross-workspace or normal-data access | Opaque token lookup, request context, no fallback, path containment, response redaction |
| Newly added/unreviewed HTTP route | Method-specific default deny plus startup and test completeness checks |
| Dangerous action hidden behind an allowed page | Endpoint-specific action allowlists before route code |
| Cross-site mutation | Session-bound CSRF plus Origin/Referer validation |
| Oversized or hostile input | 1 MiB body cap, no multipart, bounded fields/lists, finite/range checks, URL/network-field rejection |
| Multi-process request abuse | Transactional SQLite fixed-window rate counters |
| Concurrent quota bypass | Transactional reservations combined with live entity counts |
| Unbounded storage | Entity limits, SQLite page limit, uploads/exports blocked |
| SSRF, DNS rebinding, redirects, private probing | Deny-all application egress and blocked Moonraker/thumbnail actions |
| Host-file access or arbitrary download | Dangerous routes blocked; no visitor paths; workspace containment and symlink rejection |
| Error/token/path disclosure | Stable safe errors, generic logging, correlation IDs, response/redirect redaction |
| Clickjacking, MIME confusion, sensitive caching | Frame denial, nosniff, no-store, same-origin referrer policy |

## Residual risks and required deployment controls

- Container/firewall egress denial is still required as defense in depth.
- Trusted-proxy configuration, allowed hostnames, TLS termination, HSTS verification, and request concurrency/timeouts belong to staging deployment.
- Rate limits use direct source address unless proxy trust is explicitly enabled; distributed multi-host deployments would need a shared control database or external limiter.
- A process suspended beyond the renewable lease interval is subject to the documented lease-recovery assumption.
- Inline scripts/styles prevent a broad strict CSP without template refactoring.
- The foundation session is a temporary bearer identity, not user authentication or persistent account security.
- The Phase 5 `basic-v1` seed passes manifest, quota, integrity, foreign-key, and no-remote-destination checks. Future seed versions must preserve that contract. Phase 6 simulation must never weaken network deny.
- Logging/metrics aggregation, alerting, DDoS protection, image minimization, secret injection, read-only filesystem policy, and operational cleanup scheduling remain later deployment work.

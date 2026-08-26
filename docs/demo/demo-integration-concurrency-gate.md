# Demo workspace Phase 3.5 integration and concurrency gate

Status: gate evidence for the foundational runtime only. This is not a public-readiness assessment.

## Base reconciliation

The feature branch started the gate at `bdaba47` and is based directly on authoritative `origin/main` `96af75c` (v0.5.0); their merge base is `96af75c`. The local `main` is stale. `resume/phase6-reconcile` at `965b609` is an ancestor of `origin/main` and has no commits absent from the feature branch, so it is neither newer nor a merge source. The unmerged `fix/backup-tool-permissions` branch is unrelated future release work and was not speculatively integrated. No merge or rebase was required.

## Characterized previous behavior

Deterministic event-driven tests against the prior `O_EXCL` implementation established this matrix:

| Collision | Previous result |
| --- | --- |
| Same workspace: read/read | Second request HTTP 409 |
| Same workspace: read/ordinary mutation | Second request HTTP 409 |
| Same workspace: ordinary mutation/mutation | Second request HTTP 409 |
| Different workspaces | Normal HTTP 200 results |
| Ordinary request/reset | Reset HTTP 409, then 200 after teardown |
| Ordinary request/cleanup | Cleanup skipped the busy workspace |
| Ordinary request/direct expiration | Expiration could incorrectly mark the active workspace expired; deletion was blocked |
| Concurrent resets | First proceeded; second received controlled HTTP 410 |
| Request during resetting/deleting | Controlled HTTP 410 |
| Route exception | HTTP 500; teardown released the lock |

The ordinary-request 409 behavior was safe but unsuitable for normal browser fan-out. Direct expiration during an active request was also an exclusivity gap.

## Gate design and resulting behavior

Ordinary requests now acquire renewable rows in `demo_workspace_leases`. Multiple leases may coexist, including across processes. Lease acquisition atomically requires an active, unexpired workspace. Lifecycle writers use an immediate registry transaction to purge expired leases, require zero live leases, and perform the expected-state transition. A new reader and a lifecycle writer therefore cannot both win the boundary race.

Same-workspace read/read, read/mutation, and mutation/mutation tests now receive their normal HTTP 200 route result. Different workspaces remain independent. Reset, direct expiration, and deletion fail fast with `WorkspaceBusy` while a request lease is active; the reset route maps that to HTTP 409. Cleanup skips and retries on a later pass. Requests in resetting/deleting/error/expired states receive HTTP 410. A failed route releases both tracked SQLite connections and its request lease during teardown.

Lease lifetime defaults to 120 seconds with a 30-second heartbeat. A killed subprocess was proven to leave a visible stale lease, which stopped reset until an injected clock crossed the bounded expiry; reset then succeeded. Tests use events and bounded future/subprocess timeouts, not unbounded waits or sleep-based races.

## Remaining boundary

Registry leases protect request versus lifecycle replacement. They do not serialize ordinary SQLite writes with one another; SQLite retains its normal short transaction and busy-timeout behavior. Long-running work must keep heartbeat scheduling viable. A host suspended beyond the lease duration could make an in-flight worker's lease appear stale after resume; deployment supervision should terminate stale workers before cleanup. Full route authorization, CSRF, rate limits, abuse controls, and public deployment hardening remain Phase 4 and Phase 7 work.

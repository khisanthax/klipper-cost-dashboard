# Demo workspace lifecycle

The data root contains immutable `seeds/<version>/kcd.db`, mutable `workspaces/<random-id>/`, and the separate `registry/demo_registry.db`. Registry paths are simple relative internal names; cookie and request values are never used as paths.

States are `creating`, `active`, `resetting`, `expired`, `deleting`, and `error`. State changes use SQLite transactions and expected-state predicates. Creation validates the seed, clones into `.creating-*`, creates namespaced cache/export/temp/upload directories, validates SQLite, inserts a creating record, atomically renames the directory, then activates it. Failure removes the record and temporary/final partial data.

Resolution hashes the cookie, performs an indexed registry lookup, uses constant-time hash comparison, requires `active`, evaluates both deadlines, validates containment/symlinks/database integrity, and returns an immutable `WorkspaceContext`. Before binding storage, an ordinary request atomically inserts a renewable registry lease while rechecking state and expiration. It then re-resolves the session under that lease. Multiple leases may coexist for the same workspace, so overlapping reads and ordinary mutations receive their normal route result rather than a coordination `409`. Requests for different workspaces also remain independent. Activity writes are throttled and capped at the absolute deadline.

Reset, expiration, deletion, and cleanup are exclusive lifecycle writers. In one `BEGIN IMMEDIATE` registry transaction they purge stale leases, require zero active leases, and change the expected state. That transaction prevents a new request lease from racing the state change. Reset closes request-owned database handles, transitions to `resetting`, builds a fresh `.resetting-*` clone, renames the old directory aside, publishes the new directory, and removes the old copy. Publication failure restores the previous directory. A successful reset preserves token/workspace identity, increments `reset_count`, records the current seed, and returns to `active`.

Cleanup first selects due active records, then transactionally rechecks expiration, active leases, and state before claiming each record. This prevents deleting a workspace that was touched after selection or is still serving an earlier request. Leased workspaces remain active for the next pass. Cleanup transitions through `expired` and `deleting`, validates containment and rejects all symlinks, removes only that directory, then deletes the registry row. Missing directories are treated idempotently. Failures are retained as `error`; raw tokens are never logged or printed.

Request teardown closes tracked SQLite connections before releasing its lease. The lease defaults to 120 seconds and renews every 30 seconds; both values are configurable, with heartbeat required to be shorter than lifetime. A worker crash therefore blocks lifecycle work only until its bounded lease expires. Lifecycle calls do not wait, so reset returns controlled HTTP `409` while an ordinary request is active; cleanup skips the workspace. A request arriving after `resetting`, `expired`, `deleting`, or `error` receives controlled session-state HTTP `410`. Two resets are controlled by the state transition: one proceeds and the other receives `410`. There are no unbounded lock waits.

Operator commands:

```text
python -m kcd demo list
python -m kcd demo cleanup --dry-run
python -m kcd demo cleanup
python -m kcd demo validate
```

No scheduler is installed in this batch. A later deployment may invoke cleanup externally after Phase 7 hardening.

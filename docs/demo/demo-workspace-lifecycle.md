# Demo workspace lifecycle

The data root contains immutable `seeds/<version>/kcd.db`, mutable `workspaces/<random-id>/`, and the separate `registry/demo_registry.db`. Registry paths are simple relative internal names; cookie and request values are never used as paths.

States are `creating`, `active`, `resetting`, `expired`, `deleting`, and `error`. State changes use SQLite transactions and expected-state predicates. Creation validates the seed, clones into `.creating-*`, creates namespaced cache/export/temp/upload directories, validates SQLite, inserts a creating record, atomically renames the directory, then activates it. Failure removes the record and temporary/final partial data.

Resolution hashes the cookie, performs an indexed registry lookup, uses constant-time hash comparison, requires `active`, evaluates both deadlines, validates containment/symlinks/database integrity, and returns an immutable `WorkspaceContext`. Activity writes are throttled and capped at the absolute deadline.

Reset transitions to `resetting`, closes request-owned database handles, takes an O_EXCL lifecycle lock, builds a fresh `.resetting-*` clone, renames the old directory aside, publishes the new directory, and removes the old copy. Publication failure restores the previous directory. A successful reset preserves token/workspace identity, increments `reset_count`, records the current seed, and returns to `active`.

Cleanup first selects due active records, then transactionally rechecks and claims each expiration. This prevents deleting a workspace that was touched after selection. It transitions through `expired` and `deleting`, validates containment and rejects all symlinks, removes only that directory, then deletes the registry row. Missing directories are treated idempotently. Failures are retained as `error`; raw tokens are never logged or printed.

Operator commands:

```text
python -m kcd demo list
python -m kcd demo cleanup --dry-run
python -m kcd demo cleanup
python -m kcd demo validate
```

No scheduler is installed in this batch. A later deployment may invoke cleanup externally after Phase 7 hardening.

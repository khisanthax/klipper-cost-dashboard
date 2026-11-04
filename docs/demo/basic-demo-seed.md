# Basic Demo seed

Status: Phase 5 deterministic content. This dataset does not make the demo public-ready and does not simulate active printers.

## Dataset

`basic-v1` contains three local-only printer records, four filament profiles, three hourly-rate profiles, three projects, 18 historical jobs, 12 project assignments, three manual jobs, three planned items, and three informational system events. History includes completed, failed, and cancelled outcomes, pause/runout examples, multiple materials, and varied costs. Reports begin with an empty cache and derive their data from workspace-local history.

Printer records contain no Moonraker URL, hostname, IP address, port, external client identity, credential, thumbnail URL, callback, or webhook. The seed contains no mutable cache, export, temporary, or upload directory; those directories are created only in a visitor workspace clone.

## Generation

From the repository root, generate into a new absolute demo data root:

```text
python tools/create_demo_seed.py --data-root /absolute/path/to/demo-data --seed-name basic-v1
```

The generator refuses an unsafe seed name, a symlinked data root, or an existing destination. It builds through the current SQL migrations, uses stable IDs and timestamps, checks SQLite integrity and foreign keys, writes `seed-manifest.json`, validates the result, and atomically publishes the seed. It never overwrites or migrates an existing seed.

The manifest records the format, seed name, fixed generation timestamp, entity counts, SQLite file hash, stable logical-content hash, integrity result, foreign-key result, and remote-printer count. Two builds using the same source and SQLite implementation are byte-reproducible; the logical digest is the cross-version content identity.

## Startup and lifecycle validation

The normal demo seed validator remains compatible with minimal development fixtures. When a seed manifest is present, startup additionally verifies its database hash, logical digest, counts, integrity, foreign keys, seed identity, remote-printer count, configured entity quotas, and workspace-size quota. A mismatch fails startup without creating a visitor workspace or falling back to normal data.

Reset clones the configured seed again, preserves session and workspace identity, and restores every Basic Demo record. Existing workspaces keep their recorded seed version until reset. Updating Basic Demo content requires a new seed version rather than overwriting `basic-v1` in place.

## Deliberate exclusions

- No active or simulated print state; simulation belongs to Phase 6.
- No remote thumbnails or uploaded assets.
- No pre-generated report cache.
- No real printer/client identifiers or API credentials.
- No public landing, start, reset, or expired-session UX.
- No deployment, proxy, TLS, firewall, monitoring, or scheduler configuration.

# Completion-program read-only production verification

Checked at **2026-11-05T13:39:48Z**, repeated with thumbnail hash verification at
**13:51:35Z**, on vaultwarden. This is a baseline check,
not a deployment report. No print command, event replay, credential/configuration
change, service restart or SQL mutation was performed.

Docker independently reports the existing production image:

```text
ghcr.io/khisanthax/klipper-cost-dashboard:v0.7.3@sha256:006588a2d1f50ca2db977a950cdae56266d76aa166d7279d5b1e7dea0264eb0d
```

SQLite was opened with `mode=ro` and `PRAGMA query_only=ON`, without importing
application code. Integrity returned `ok`, foreign-key violations were zero and
the database retained **1,047 jobs**. HTTP `/health` passed all five readiness
checks with four configured printers and schema `0008_pricing_provenance`.
All **316 permanent thumbnail objects** matched their saved byte sizes and SHA-256
hashes; there are **435 archived job references**. No backfill, source recovery or
cache cleanup was requested.

## Existing SV07 recovery remains intact

Both rows were already recovered during the prior investigation. They were
inspected rather than repaired again.

| Import identity | Stable job UID | Stored outcome | Duration seconds | Filament mm | Saved time/material/total |
| --- | --- | --- | ---: | ---: | --- |
| SV07:000240 | ece506d3-55de-51ed-b93b-aa7b321f5126 | canceled | 996 | 454.66522000001265 | all null |
| SV07:000243 | 6b0dc0d4-7645-5cab-a16a-33924b9d296b | canceled | 820 | 280.6652200000509 | all null |

Null saved costs mean unpriced. The v0.7.3 reader can still display a current-rate
estimate for null cost fields. The candidate reader correction in PR 61 must
still pass review, release and deployment before claiming the live dashboard
shows both **Canceled / Unpriced**. No historical recalculation was performed.

## Current Moonraker observations

Each configured endpoint was queried from inside the Master container, using
only `/printer/objects/query`. All four connected, and `_KCD_VARS.printer_name`
matched the registered identity.

| Registered identity | Observed print_stats state | Loaded identity |
| --- | --- | --- |
| Ender5P | complete | Ender5P |
| MercuryTwo | standby | MercuryTwo |
| SV07 | cancelled | SV07 |
| SV08 | complete | SV08 |

These are point-in-time observations, not a forced physical print lifecycle test.
The earlier [v0.7.3 deployment evidence](v0.7.3-deployment-2026-11-03.md) remains
the authoritative deployed image, backup and rollback report. No new release,
deployment or rollback rehearsal was performed while protected-main review is
pending. Fresh coordinated backups and isolated restore checks remain mandatory
immediately before an authorized eligible deployment.

The retained host tag `kcd-rollback:v0.7.2-20261103` was independently inspected
again on November 5 and still resolves to the documented image ID
`sha256:6348b7891dd50a0b52dcb3240a70ff97fcb76787dc57a0471593ba9ec9f7296d`.
It was not started against production data.

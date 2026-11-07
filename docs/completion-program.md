# KCD completion and release execution plan

> Historical November 5 completion-program record. Current November 7 production is v0.12.0; see [LOGBOOK](../LOGBOOK.md) and [actual measurement/pricing deployment](v0.12.0-deployment-2026-11-07.md). The earlier program outcomes below are retained as dated evidence.



**Policy update:** The owner-authorized [solo-maintainer policy](solo-maintainer-release-policy.md) supersedes human-review blockers. Required PRs and strict CI remain enforced. v0.7.4 through v0.11.0 completed verified deployment; every source PR is resolved. Optional services remain inactive and demo stays isolated by owner choice.
The initial inventory was checked on 2026-11-05 at main `7892765`.
This plan coordinates existing branches. The unified roadmap remains the product
roadmap and root LOGBOOK records completed operations and evidence.

## Verified state

Production is verified v0.11.0 at immutable index
`sha256:9f851f76cb2be97216f557721a09ec6e3c2d7fc3a684bd2dc4e7c0d2c5eedebf`, revision `23977013`, with 1,049 preserved jobs,
four connected/identity-verified printers, schema `0011_inventory_recovery`,
317 verified objects and 436 archived references. Advanced remains unchanged;
Orders/inventory stay empty. Both SV07 recovered rows show Canceled/Unpriced.
Optional browser administration/demo code is shipped but its separate services
remain inactive, with normal runtime and no new published port/listener.
See [actual deployment and complete acceptance matrix](v0.11.0-deployment-2026-11-05.md).
All source PRs 61–70 are resolved, including superseded 58 via 71; each validated
cohort completed normal merge, publication, backup/restore, deployment and live
preservation checks. Demo remains isolated by explicit owner choice.
Existing local test edits and production configuration are retained.

The owner replaced the unavailable-human-review gate on November 5 with the
[solo-maintainer policy](solo-maintainer-release-policy.md). Main requires PRs,
strict successful `validate` and `compile` checks, resolved threads and final
diff/migration inspection; required human approvals are zero. Existing deletion
and force-push protections remain. Normal merges are used without admin bypass.

Remote branch inventory additionally found `feature/sql-hardening` with one
unmerged January 3 commit, `0624952`, after its earlier PR 44 had merged. Its
useful local-template ignore rules already exist on main. Its automatic settings
bootstrap is superseded by today's explicit registration contract: fresh settings
start empty and strict SQL never bootstraps legacy business-state files. Importing
its old storage implementation would reintroduce seeded identities and remove
current guards. The branch is retained as evidence; no new feature or stale
runtime implementation is merged from it. The remaining unmerged remote branches
map to the inventoried PRs, including the preserved v0.7.2 documentation branch.

## Work inventory and dependency order

| Order | Existing PR | Dependency and completion action |
| --- | --- | --- |
| 1 | 61 · lifecycle recovery | Complete: merged normally with governance PR 71, published/deployed v0.7.4 at 237ccc88; fresh backup, isolated restore and live Canceled/Unpriced checks passed. |
| 2 | 58 · older handoff | Complete: useful AGENTS and historical v0.7.2 evidence preserved in merged PR 71; stale PR 58 closed with linked handoff. |
| 3 | 62 · printer pages | Complete in verified v0.8.0: all four actual printer pages, recorded values, comparisons and responsive layouts checked. |
| 4 | 63 · browser installer | Complete in v0.11.0: real browser/local/strict-SSH fixture acceptance and actual existing-client CLI dry-runs passed. Optional separate administrator service remains inactive. |
| 5 | 65 · modes | Complete in verified v0.9.0: Advanced default preserved; modes/route/data preservation tested in fixtures and production read-only acceptance. |
| 6 | 66 · orders | Complete in verified v0.9.0: Business lifecycle/financial boundaries tested in fixtures; production Advanced GET boundary and empty additive schema verified without customer mutations. |
| 7 | 67 · inventory | Complete in verified v0.10.0: exact milligram ledger/replay/corrections/concurrency/archive recovery tested in fixtures; additive empty 0010/0011 schema and preserved production history verified. |
| 8 | 68 · estimates | Complete in verified v0.8.0: signed reviewed plans and atomic SQL fixes validated in fixtures; production stateless previews and unchanged history verified. |
| 9 | 69 · export | Complete in verified v0.8.0: consistent private production export verified against SQL membership/costs/provenance without mutation. |
| 10 | 70 · reports | Complete in verified v0.10.0: SQL/CSV/dual fixtures and real per-printer production counts/outcomes/saved costs verified without historical mutation. |
| 11 | 64 · demo | Complete optional code and isolated staging in v0.11.0: TLS/proxy/egress/workspace isolation/recovery passed. Owner chose to keep demo isolated; no public endpoint or production demo activation. |

Orders migration 0009 and inventory migrations 0010/0011 are additive. Migration
0011 preserves 0010 and adds correction/recovery records for existing installations. Verify upgrade
and restored-backup readiness after each integration. Preserve migration filenames
already present in candidates, and check combined startup against current main.
No financial recompute or production recalculation belongs to this program.

## Implementation and review checkpoints

1. Make scoped fixes on the existing feature branches and retain their previous
   evidence. Add regression tests for actual gaps discovered by the review.
2. Update each branch's logbook and relevant operator documentation. Mark a draft
   ready only after its documented scope and required checks pass.
3. Inspect the final diff and migrations, record findings and resolve them on the
   branch. Merge normally after required checks pass under the solo policy.
4. Merge one approved feature at a time. Rebase the next dependent candidate onto
   approved main, resolve overlaps in app.py, templates, migration state and tests,
   and revalidate. Retarget 63 after 61 and 66 after 65.
5. Use v0.7.4 for the existing lifecycle patch. Subsequent feature integrations
   receive minor versions in sequence after review. The exact later tags follow
   the integrated scope and release notes.

## Required release sequence

The completed release cohorts separated risk and migrations: v0.7.4 contains lifecycle
and governance at 237ccc88; v0.8.0 groups reviewed estimates/atomic project
mutations (68), isolated project export (69) and printer pages (62). The confirmed
project lost-write and canonical-identity repairs precede Business workflows.
The subsequent completed cohorts were Modes/Orders (65/66, v0.9.0), inventory/reporting (67/70,
v0.10.0), then optional private administration/demo tooling (63/64, v0.11.0).
Secure each tag at its validated cohort commit before later main integrations.
Each production replacement requires its own fresh verified backup and evidence.
Shipping optional service code does not activate a public demo or administrative
listener; those operational boundaries remain explicit.

For each eligible merge, require full Release Validation and Compile checks.
Use the existing tag-triggered GHCR workflow, which validates before publishing
linux/amd64, linux/arm64 and linux/arm/v7. Verify the index and runnable platform
manifests and pin the production override to the immutable index.

Before deploying, check current printer activity, take the coordinated SQL/object
archive, verify private mode and SHA-256, and test the restored copy in isolation.
Retain the previous image, private override and offline archive. Compare resolved
Compose configuration and recreate only KCD in a safe window. Verify actual live
OCI version/revision and image ID, SQL readiness/integrity/foreign keys, history
preservation, permanent object hashes, all four printer identities and the feature
through its normal browser flow. Attach sanitized deployment and rollback evidence
to the release and commit the actual outcome to LOGBOOK.

## Completion and operational boundaries

The coordinated source backlog is complete: normal protected merges, releases
v0.7.4 through v0.11.0, verified backups/rollback rehearsals and live preservation
are recorded. There is no unavailable-human-review blocker. See the final
[workstream acceptance matrix](v0.11.0-deployment-2026-11-05.md).
Administration remains intentionally inactive pending private least-privilege
operational configuration and an intended idle-window preview. The owner chose
to keep demo isolated; no public deployment or production demo mode is included.
Actual physical fresh-Pi/power-loss/native-toolbar/assistive validation remains
distinct from automated fixtures and existing-client read-only dry-runs.

The [November 5 production check](completion-program-production-2026-11-05.md)
confirms all four loaded identities, both previously recovered SV07 rows, SQL
integrity/readiness and all 316 permanent object hashes. PR 58 is now closed with
a linked handoff to PR 71; its useful governance and v0.7.2 evidence are retained.

Release Validation now defaults the disposable image rehearsal to the current
pinned v0.7.3 baseline and also retains a v0.7.2 compatibility run. Both paths test
baseline → candidate → rollback and compare post-upgrade SQL tables after the
old image returns. This is isolated CI validation, not a production rollback.

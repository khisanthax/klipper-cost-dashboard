# KCD completion and release execution plan


**Policy update:** The owner-authorized [solo-maintainer policy](solo-maintainer-release-policy.md) supersedes earlier human-review blockers. Required PRs and strict CI remain enforced. PRs 61/71 are merged and v0.7.4 is verified deployed.
The initial inventory was checked on 2026-11-05 at main `7892765`.
This plan coordinates existing branches. The unified roadmap remains the product
roadmap and root LOGBOOK records completed operations and evidence.

## Verified state

Production is now verified v0.7.4 at immutable index
`sha256:9e11c14a716a52df6c27fa6245a3d2d584b9484123c63b4905eaf556d779186e`,
revision `237ccc88`, with 1,047 preserved jobs, four connected/identity-verified
printers, SQL schema `0008_pricing_provenance`, 316 verified objects and 435 archived
references. Both SV07 recovered rows visibly show Canceled and Unpriced. See
[actual deployment and rollback evidence](v0.7.4-deployment-2026-11-05.md).
PR 58 is superseded/closed; 61/71 and later 68/69 have merged normally. The latter
feature code is queued for v0.8.0 and is not in the deployed v0.7.4 image. The
remaining candidates and release cohorts below continue through strict CI.
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
| 3 | 62 · printer pages | Finish missing-value and filter/pagination coverage. Rebase onto approved main, resolve shared telemetry changes and rerun all checks. |
| 4 | 63 · browser installer | Depends on 61. Finish scoped discovery, effective macro inspection and actionable repair diagnostics through the same CLI engine. Keep service and destructive host operations CLI-only. |
| 5 | 65 · modes | Personal now includes Projects under the Nov 5 directive. Verify direct routes, reversible settings and complete data preservation. |
| 6 | 66 · orders | Depends on 65. Preserve Business gating after the modes correction. Complete transitions, partial financial inputs and deleted-project handling. |
| 7 | 67 · inventory | SQL migration 0010. Review milligram ledger, exact-once replay, linked corrections, archival, concurrent writes and archive recovery. |
| 8 | 68 · estimates | Finish reviewed estimate-to-planned conversion with replay protection and preserved assumptions. Strengthen malformed/oversized/missing-metadata tests. |
| 9 | 69 · export | Complete explicit recorded overrides and original price provenance, versioned schema examples and atomic project isolation tests. |
| 10 | 70 · reports | Complete date/printer filters, known material/time costs, unavailable measurements and monthly comparisons across SQL/CSV/dual. |
| 11 | 64 · demo | Finish isolated TLS/proxy tests, fail-closed host/auth/egress boundaries, expiration and interrupted-workspace recovery. Public exposure requires an approved target and security configuration. |

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

Current release cohorts separate risk and migrations: v0.7.4 contains lifecycle
and governance at 237ccc88; v0.8.0 groups reviewed estimates/atomic project
mutations (68), isolated project export (69) and printer pages (62). The confirmed
project lost-write and canonical-identity repairs precede Business workflows.
Next cohorts are Modes/Orders (65/66, v0.9.0), inventory/reporting (67/70,
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

## Current blockers and continuing work

Human review is no longer a blocker. Finish required CI and normal incremental
merges, then publish and deploy verified releases with coordinated backups.
Public demo publication still requires explicit approval of its concrete endpoint
and security configuration. Isolated implementation and staging continue without
public exposure.

The [November 5 production check](completion-program-production-2026-11-05.md)
confirms all four loaded identities, both previously recovered SV07 rows, SQL
integrity/readiness and all 316 permanent object hashes. PR 58 is now closed with
a linked handoff to PR 71; its useful governance and v0.7.2 evidence are retained.

Release Validation now defaults the disposable image rehearsal to the current
pinned v0.7.3 baseline and also retains a v0.7.2 compatibility run. Both paths test
baseline → candidate → rollback and compare post-upgrade SQL tables after the
old image returns. This is isolated CI validation, not a production rollback.

# KCD completion and release execution plan


**Policy update:** The owner-authorized [solo-maintainer policy](solo-maintainer-release-policy.md) supersedes earlier human-review blockers. Required PRs and strict CI remain enforced. v0.7.4 through v0.10.0 completed verified deployment; all requested source work is implemented. The final v0.11.0 cohort passed CI, merged and published; production deployment is deferred by an unresolved paused MercuryTwo print. Actual runtime remains v0.10.0.
The initial inventory was checked on 2026-11-05 at main `7892765`.
This plan coordinates existing branches. The unified roadmap remains the product
roadmap and root LOGBOOK records completed operations and evidence.

## Verified state

Production is now verified v0.10.0 at immutable index
`sha256:dad13e3622dc253b324cf24ff9ca23c8c1d19c6b145ca4fd18efad6883c16822`,
revision `d5df3a81`, with 1,047 preserved jobs, four connected/identity-verified
printers, SQL schema `0011_inventory_recovery`, 316 objects and 435 archived
references. Inventory and Orders remain empty, Advanced remains unchanged.
Both SV07 recovered rows show Canceled and Unpriced. Actual per-printer monthly
counts/outcomes and saved cost subtotals matched SQL. See
[deployment and rollback evidence](v0.10.0-deployment-2026-11-05.md).
PR 58 is superseded/closed; 61/71, 68/69/62, 65/66 and 67/70 have merged normally
and shipped in their recorded cohorts. Private administration 63 and isolated-demo 64 are normally merged and published at 23977013 in v0.11.0. All source PRs are resolved. A guarded replacement stopped before container recreation at an active/paused real print; [current status](v0.11.0-release-status-2026-11-05.md) distinguishes published v0.11.0 from running v0.10.0. Shipping optional code does not activate either service or public exposure.
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
| 4 | 63 · browser installer | Source complete and merged normally at ce42e1d after strict validation. Shared-engine configured local/SSH discovery, inspection, registration/repair, credential verification and private backup previews are packaged for v0.11.0. Advanced host/macro/destructive operations remain deliberately CLI-only; optional authenticated listener stays off in production. |
| 5 | 65 · modes | Complete in verified v0.9.0: Advanced default preserved; modes/route/data preservation tested in fixtures and production read-only acceptance. |
| 6 | 66 · orders | Complete in verified v0.9.0: Business lifecycle/financial boundaries tested in fixtures; production Advanced GET boundary and empty additive schema verified without customer mutations. |
| 7 | 67 · inventory | Complete in verified v0.10.0: exact milligram ledger/replay/corrections/concurrency/archive recovery tested in fixtures; additive empty 0010/0011 schema and preserved production history verified. |
| 8 | 68 · estimates | Complete in verified v0.8.0: signed reviewed plans and atomic SQL fixes validated in fixtures; production stateless previews and unchanged history verified. |
| 9 | 69 · export | Complete in verified v0.8.0: consistent private production export verified against SQL membership/costs/provenance without mutation. |
| 10 | 70 · reports | Complete in verified v0.10.0: SQL/CSV/dual fixtures and real per-printer production counts/outcomes/saved costs verified without historical mutation. |
| 11 | 64 · demo | Source, isolated staging, strict CI, normal merge and publication complete in v0.11.0; normal Master replacement awaits a safe inactive window. TLS/proxy/host/auth/egress, quotas, reset/expiry/interruption and cross-visitor boundaries are tested. Owner explicitly chose to keep it isolated; no public endpoint or pending approval blocker. |

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

## Final packaging and deployment gate

All requested source implementations are merged and v0.11.0 published after exact-head CI. The safe-window guard retained the v0.10.0 runtime because MercuryTwo job 00017B is paused/in_progress. No printer command or uncertain finalization is authorized to bypass this condition. After its normal terminal event, refresh baseline/backup and repeat isolated candidate/rollback checks before the KCD-only replacement and live verification. No source item is awaiting an unavailable human reviewer.
The owner explicitly chose to keep the demo isolated. Complete implementation and
isolated staging without public exposure or production demo activation.

The [November 5 production check](completion-program-production-2026-11-05.md)
confirms all four loaded identities, both previously recovered SV07 rows, SQL
integrity/readiness and all 316 permanent object hashes. PR 58 is now closed with
a linked handoff to PR 71; its useful governance and v0.7.2 evidence are retained.

Release Validation now defaults the disposable image rehearsal to the current
pinned v0.7.3 baseline and also retains a v0.7.2 compatibility run. Both paths test
baseline → candidate → rollback and compare post-upgrade SQL tables after the
old image returns. This is isolated CI validation, not a production rollback.

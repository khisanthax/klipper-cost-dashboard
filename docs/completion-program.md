# KCD completion and release execution plan

Rechecked 2026-11-05 against GitHub, current main `7892765`, and production.
This plan coordinates existing branches. The unified roadmap remains the product
roadmap and root LOGBOOK records completed operations and evidence.

## Verified state

Production is v0.7.3 at immutable index
`sha256:006588a2d1f50ca2db977a950cdae56266d76aa166d7279d5b1e7dea0264eb0d`.
The live SQL health check passed all five checks with 1,047 jobs, four printers and
schema `0008_pricing_provenance`. The repository has no open issues. PRs 58 and
61–70 remain open. Their current heads passed the required checks before this
completion review. Existing local test edits and configuration are retained.

GitHub's active main ruleset requires one approving review and approval of the
latest push by another eligible reviewer. The collaborator inventory currently
contains only `khisanthax`, who authored these PRs. A review request cannot be
assigned to an eligible collaborator with the current access inventory. A
repository owner must make an eligible reviewer available. CI and internal
engineering review do not satisfy that external gate.

## Work inventory and dependency order

| Order | Existing PR | Dependency and completion action |
| --- | --- | --- |
| 1 | 61 · lifecycle recovery | Review identity, delivery and event replay safety. Verify previously repaired SQL rows read-only. After eligible approval, merge and validate main, tag v0.7.4, publish, back up, deploy and verify Canceled/Unpriced rows. |
| 2 | 58 · older handoff | Recover the missing root AGENTS instructions and retain v0.7.2 evidence as historical documentation. Supersede the stale PR with a linked current handoff. Preserve the current v0.7.3 baseline. |
| 3 | 62 · printer pages | Finish missing-value and filter/pagination coverage. Rebase onto approved main, resolve shared telemetry changes and rerun all checks. |
| 4 | 63 · browser installer | Depends on 61. Finish scoped discovery, effective macro inspection and actionable repair diagnostics through the same CLI engine. Keep service and destructive host operations CLI-only. |
| 5 | 65 · modes | Personal now includes Projects under the Nov 5 directive. Verify direct routes, reversible settings and complete data preservation. |
| 6 | 66 · orders | Depends on 65. Preserve Business gating after the modes correction. Complete transitions, partial financial inputs and deleted-project handling. |
| 7 | 67 · inventory | SQL migration 0010. Review milligram ledger, exact-once replay, linked corrections, archival, concurrent writes and archive recovery. |
| 8 | 68 · estimates | Finish reviewed estimate-to-planned conversion with replay protection and preserved assumptions. Strengthen malformed/oversized/missing-metadata tests. |
| 9 | 69 · export | Complete explicit recorded overrides and original price provenance, versioned schema examples and atomic project isolation tests. |
| 10 | 70 · reports | Complete date/printer filters, known material/time costs, unavailable measurements and monthly comparisons across SQL/CSV/dual. |
| 11 | 64 · demo | Finish isolated TLS/proxy tests, fail-closed host/auth/egress boundaries, expiration and interrupted-workspace recovery. Public exposure requires an approved target and security configuration. |

Orders migration 0009 and inventory migration 0010 are additive. Verify upgrade
and restored-backup readiness after each integration. Preserve migration filenames
already present in candidates, and check combined startup against current main.
No financial recompute or production recalculation belongs to this program.

## Implementation and review checkpoints

1. Make scoped fixes on the existing feature branches and retain their previous
   evidence. Add regression tests for actual gaps discovered by the review.
2. Update each branch's logbook and relevant operator documentation. Mark a draft
   ready only after its documented scope and required checks pass.
3. Record code-review findings and request eligible reviews when the repository
   has an eligible reviewer. Address feedback on the branch.
4. Merge one approved feature at a time. Rebase the next dependent candidate onto
   approved main, resolve overlaps in app.py, templates, migration state and tests,
   and revalidate. Retarget 63 after 61 and 66 after 65.
5. Use v0.7.4 for the existing lifecycle patch. Subsequent feature integrations
   receive minor versions in sequence after review. The exact later tags follow
   the integrated scope and release notes.

## Required release sequence

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

Main merges and production releases await an eligible non-author review. Public
demo publication also awaits explicit approval of its endpoint and security
configuration. Independent implementation, tests and isolated staging continue
while those gates are pending.

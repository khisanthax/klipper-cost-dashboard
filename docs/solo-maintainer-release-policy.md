# Solo-maintainer release policy

The owner authorized this policy on November 5, 2026. It supersedes earlier
statements requiring an unavailable independent human reviewer.

Ruleset 11487956 retains required PRs, resolved review threads, deletion and
force-push protection. Human approval count is zero; last-push and extra human
approval requirements are disabled. Required `validate` and `compile` checks are
strictly enforced against current main. Compile runs for every PR/push so a
documentation-only PR cannot wait forever for a path-filtered required check.
Release Validation retains Python, SQL, Playwright, security, installer, Docker
and upgrade/rollback gates. Existing bypass actors were not expanded and are not
used to merge.

Inspect the exact final diff and migrations and fix findings before normal merge.
Validate merged main before version tagging/publication. Verify all three image
architectures and pin the immutable digest. Before production replacement, verify
activity, take a consistent private SQL/object backup and validate isolated restore;
retain the prior image/configuration. Recreate only KCD in a safe window, verify
image/revision, SQL integrity/readiness, history/objects, all printer identities
and browser behavior, and attach sanitized deployment/rollback evidence. Commit
the actual outcome to LOGBOOK. Never claim deployment from CI alone.

Public demo exposure still requires approval of a concrete target and security
configuration. Historical review-blocker entries remain evidence of the previous
policy, not current blockers.

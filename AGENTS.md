# KCD project handoff requirements

Before beginning every KCD task, read the repository root `LOGBOOK.md`. Treat it
as the authoritative project handoff record. Use its references to inspect the
current repository, review, release and production state.

Before reporting completion, append a dated entry with completed work, exact
tests and results, GitHub commits/PRs/CI/releases, deployment status and remaining
issues. Distinguish implemented, tested, awaiting review, merged, released and
deployed. Claim deployment only after verifying the live image/revision and
relevant health checks. State the verification date when using prior evidence.

Commit and push the logbook update in the task PR or a follow-up documentation PR.
Follow repository review requirements. Link the committed logbook in the final
response. Keep older entries and add dated corrections. Protect credentials,
private keys, personal information and private backup contents.

Solo-maintainer policy (owner-authorized November 5, 2026): require pull requests
and successful Release Validation and Python Compile checks, with zero required
human approvals. Inspect the final diff and migrations, resolve review findings,
and merge normally when checks pass. Do not fabricate reviews or use administrator
bypass. Carry validated changes through release and safe deployment autonomously;
public exposure/security approvals remain separate requirements.

Preserve production safety, historical pricing and printer identities. Use
disposable services for failure injection and the established recovery workflow
for authorized production operations.

# KCD project handoff requirements

Before beginning every KCD task, read the repository root `LOGBOOK.md`. Treat it
as the authoritative project handoff record. Use its references to inspect the
current repository, review, release and production state; do not assume an older
conversation or checkout is current.

Before reporting completion:

1. Append a dated logbook entry covering completed work, exact tests and results,
   GitHub commits/PRs/CI/releases, deployment status and remaining issues.
2. Distinguish implemented, tested, awaiting review, merged, released and deployed.
   Claim deployment only after verifying the running production image/revision
   and relevant health checks. Identify the timestamp of prior verification when
   reporting a historical baseline rather than performing a new live check.
3. Commit and push the logbook update to GitHub in the task PR or a follow-up
   documentation PR. Follow repository review requirements; a pushed branch is
   not a merged change. If access prevents publication, state that limitation.
4. Link the committed logbook in the completion response.

Keep previous entries intact; add dated corrections. Never put credentials,
private keys, personal data or secret-bearing logs in the logbook. Preserve the
production safety and recovery requirements documented in the repository.

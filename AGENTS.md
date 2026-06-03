## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues for `gmcnicol/sketcher`. See `docs/agents/issue-tracker.md`.

When a task is complete, commit it, push the branch, and open a normal ready-for-review PR. Do not open draft PRs unless explicitly requested. Link the relevant issue from the PR body, and after merge update or close the ticket with the merged PR and validation summary.

When the user says "lgtm" for a ready PR, treat that as approval to merge the PR, sync the local checkout with the default branch, and suggest the next issue to work on.

### Triage labels

Use the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This is a multi-context repo: a Node.js web app and a Python/uv model builder. See `docs/agents/domain.md`.

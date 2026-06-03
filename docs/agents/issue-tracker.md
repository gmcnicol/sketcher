# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` - `gh` does this automatically when run inside a clone.

## Pull request workflow

- Completed tasks should be committed, pushed, and opened as normal ready-for-review PRs. Do not use draft PRs unless the user explicitly asks for a draft.
- PR titles should be concise and prefixed with `[codex]`.
- PR bodies must cross-reference the relevant issue. Use a closing keyword such as `Closes #2` when the PR fully satisfies the issue, or `Refs #2` / `Part of #2` when it is an intentional partial slice.
- PR bodies should include a short summary and the validation commands that were run.
- If a merged PR did not automatically close its linked issue, update the issue manually after syncing `main`.

## After merge

1. Fetch and fast-forward local `main` to `origin/main`.
2. Inspect the merged PR and linked issue state with `gh pr view <number>` and `gh issue view <number> --comments`.
3. Comment on the linked issue with the merged PR URL, a short summary, and validation notes when that context is not already clear from the PR.
4. Close the issue if the merged PR satisfies the acceptance criteria. Leave it open and comment with remaining work if the PR was only a partial slice.
5. When identifying the next task, ignore closed issues and respect any `Blocked by` relationships in the issue body.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

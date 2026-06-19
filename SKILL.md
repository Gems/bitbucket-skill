---
name: bitbucket
description: >
  Interact with Bitbucket — create PRs, list PRs, view PR details, merge, decline, read comments, view pipelines,
  CI-gated merge (poll the PR pipeline and merge when green), code review with inline comments, manage environments
  and variables. Use when user says "/bitbucket pr", "/bitbucket create-pr", "/bitbucket list-prs",
  "/bitbucket pipelines", "/bitbucket merge-when-green", "/bitbucket ship", asks to open a PR and get it merged once
  CI passes / merge when the pipeline is green, or similar Bitbucket-related requests.
argument-hint: "[command] [args]"
allowed-tools: Bash(python3 *), Bash(~/.claude/skills/bitbucket/scripts/*)
---

# Bitbucket Skill

Lightweight Bitbucket CLI for Claude Code. Uses a Python script that calls Bitbucket Cloud REST API 2.0 directly and returns **compact output** to minimize token usage.

When invoked with arguments (e.g. `/bitbucket create-pr 123`), run the script
from whichever location exists (see **Script Location** below) with `$ARGUMENTS`.

## Script Location

The script may live in either of these places — **look in both and use the one
that exists; if both exist, prefer the repo-attached copy**:

1. Repo-attached (preferred): `<repo>/.claude/skills/bitbucket/bitbucket_api.py`
2. Global: `~/.claude/skills/bitbucket/bitbucket_api.py`

Invoke the resolved path, e.g. `python3 <resolved>/bitbucket_api.py <command> [args]`.

## Config

The config is resolved the same way — **look in both, prefer the repo-attached
one if both exist**:

1. Repo-attached (preferred): `bitbucket.config` next to the script (`<repo>/.claude/skills/bitbucket/bitbucket.config`)
2. Global: `~/.claude/bitbucket.config`

The script already applies this repo-first resolution automatically.

### Config Setup

Supports two auth methods:

**Option 1: App Password (recommended)**
```json
{
  "username": "your_bitbucket_username",
  "app_password": "your_bitbucket_app_password",
  "workspace": "your-workspace",
  "repo_slug": "your-repo"
}
```
Create: Bitbucket → Personal settings → App passwords (scopes: Repositories Read/Write, Pull requests Read/Write).

**Option 2: Repository Access Token**
```json
{
  "access_token": "your_repository_access_token",
  "workspace": "your-workspace",
  "repo_slug": "your-repo"
}
```
Create: Repository settings → Access tokens → Create.

**Note**: `workspace` and `repo_slug` are optional — auto-detected from `git remote get-url origin`.
**Note**: Atlassian API Tokens (used for JIRA) do NOT work with Bitbucket Cloud API.

## Commands

All commands are run via Bash tool:

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py <command> [args]
```

### Create Pull Request

**Trigger**: `/bitbucket create-pr`, `/bitbucket pr`, "create PR", "open pull request"

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py create-pr "PROJ-123: Fix customer grid" --description "Summary of changes" --destination master
```

- `--description TEXT` — PR description (markdown)
- `--source BRANCH` — source branch (default: current branch)
- `--destination BRANCH` — target branch (default: master)
- `--no-close` — don't close source branch after merge
- Returns: PR number and URL

### List Pull Requests

**Trigger**: `/bitbucket list-prs`, `/bitbucket prs`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py list-prs [STATE]
```

- STATE: `OPEN` (default), `MERGED`, `DECLINED`, `SUPERSEDED`

### View Pull Request

**Trigger**: `/bitbucket get-pr <ID>`, `/bitbucket pr <ID>`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py get-pr 123
```

### Merge Pull Request

**Trigger**: `/bitbucket merge-pr <ID>`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py merge-pr 123 --strategy squash
```

- `--strategy`: `merge_commit` (default), `squash`, `fast_forward`

### Decline Pull Request

**Trigger**: `/bitbucket decline-pr <ID>`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py decline-pr 123
```

### PR Comments

**Trigger**: `/bitbucket pr-comments <ID>`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py pr-comments 123
```

### Add Comment to PR

**Trigger**: `/bitbucket add-comment <ID> <text>`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py add-comment 123 "LGTM! Ready to merge."
```

### Pipelines

**Trigger**: `/bitbucket pipelines`

```bash
python3 ~/.claude/skills/bitbucket/bitbucket_api.py pipelines [COUNT]
```

- COUNT: number of recent pipelines to show (default: 10)

**Status interpretation** — the Status column is `STATE/RESULT-or-STAGE`:
- `COMPLETED/SUCCESSFUL`, `COMPLETED/FAILED` — terminal results.
- `IN_PROGRESS/PAUSED` (or `/HALTED`) — **not running**: the pipeline succeeded up to a manual trigger step and is waiting for a human. Its Duration is wall-clock since the pause, so do **not** report it as "stuck", "slow", or "still building". Read it as "built OK, awaiting manual trigger".
- `IN_PROGRESS` with no stage — genuinely executing.
- For per-step detail, query `/pipelines/{uuid}/steps/` and read each step's `state.name` / `state.result.name`.

### Merge When Green (CI-gated merge)

**Trigger**: `/bitbucket merge-when-green`, `/bitbucket ship`, or any phrasing that asks to open a PR and get it merged once CI passes — e.g. "let's open a PR and get it merged", "ship this", "merge it once the pipeline is green", "create the PR and merge when CI passes".

This is **not** a single CLI call — it is an agent procedure that polls the PR's pipeline and merges only on success. Follow these steps in order:

1. **Resolve the PR.**
   - If a PR is named in the conversation (number/URL), use it.
   - Otherwise, if an open PR already exists for the current branch (`list-prs OPEN`), use it.
   - Otherwise create one with `create-pr` (destination = the branch's intended base, default the repo's main/default branch) — **unless the current branch _is_ the main/default branch**, in which case there is nothing to merge: stop and tell the user.

2. **Identify the PR's pipeline run.** Run `pipelines`. The PR build is the most recent run with trigger `pipeline_pullrequest_target` (branch shown as `**`) created at/after the PR's head commit. Note its `STATE/RESULT` and `Created` time.

3. **Derive a reference duration.** From the same `pipelines` list, take the most recent `COMPLETED/SUCCESSFUL` run with trigger `pipeline_pullrequest_target` and read its `Duration` (elapsed). That is the expected runtime. If there is no such prior PR run, fall back to ~10 min.

4. **Deduce the wait.** `wait ≈ reference_duration − elapsed_since_pipeline_created` (use current time vs. the run's `Created`, accounting for any timezone offset between the API's UTC timestamps and local time). Clamp to a sensible minimum (~60s). Never wait the full reference duration if the run is already partway through.

5. **Wait, then re-check.** Wait the deduced period (use `ScheduleWakeup`; the prompt for the wakeup should re-enter this procedure), then re-run `pipelines`:
   - `COMPLETED/SUCCESSFUL` → go to step 6.
   - `COMPLETED/FAILED` (or `ERROR`/`STOPPED`) → **stop, do not merge**, report the failure to the user.
   - Still `IN_PROGRESS` → deduce a short follow-up wait (e.g. 1–2 min) and repeat this step.
   - `IN_PROGRESS/PAUSED` or `/HALTED` → the build is done and waiting on a manual trigger step; treat the build as passed for merge purposes unless the manual step is the gate the user cares about — if unsure, ask.

6. **Merge.** Once the run is `COMPLETED/SUCCESSFUL`, merge with `merge-pr <ID>`. Report the merge commit and that CI was green.

Notes:
- The merge is conditioned on observed green CI — keep the confirming `pipelines` output in the transcript so the success condition is verifiable at merge time.
- Bitbucket API timestamps are UTC; convert when computing elapsed/wait against local `date`.

## PR Title Format

PR title **must** follow the format: `PROJ-XXX: Task title`

Example: `PROJ-123: Fix customer grid loading issue`

## PR Description Formatting

Bitbucket PR descriptions use **Markdown** format (not ADF like JIRA).

### Co-Authored-By Line

The text `Co-Authored-By: Claude Opus 4.6 noreply@anthropic.com` **must be italic** in the PR description.

In Markdown, wrap it with `*`:
```
*Co-Authored-By: Claude Opus 4.6 noreply@anthropic.com*
```

### PR Description Template

```markdown
## Summary

- Change description here

## Test Plan

- [ ] Test step here

---

*Co-Authored-By: Claude Opus 4.6 noreply@anthropic.com*
```

## Code Review with Inline Comments

### Single Inline Comment

Use the `bb-comment.sh` script to add inline code review comments:

```bash
~/.claude/skills/bitbucket/scripts/bb-comment.sh <pr-id> <file-path> <line-number> "comment text"
```

Example:
```bash
~/.claude/skills/bitbucket/scripts/bb-comment.sh 42 app/code/Vendor/Module/Model/Example.php 45 "Use dependency injection here"
```

### Batch Code Review

Use `bb-review.sh` for batch review with multiple comments:

```bash
~/.claude/skills/bitbucket/scripts/bb-review.sh <pr-id> <comments-file>
```

Comments file format (TSV — tab separated):
```
file_path<TAB>line_number<TAB>comment_text
```

Example:
```bash
echo -e 'src/Model.php\t45\tUse DI here\nsrc/Controller.php\t120\tAdd try/catch' > /tmp/comments.tsv
~/.claude/skills/bitbucket/scripts/bb-review.sh 42 /tmp/comments.tsv
```

Scripts auto-detect credentials from `~/.claude/bitbucket.config` or environment variables (`BB_WORKSPACE`, `BB_REPO`, `BB_USER`, `BB_APP_PASSWORD`).

## Direct API Reference

For advanced use cases not covered by the Python script, see [references/api_reference.md](references/api_reference.md).

## Notes

- Default destination branch: **master**
- Auto-detects workspace/repo from git remote if not in config
- Output is already formatted as markdown — display directly to user
- No external Python dependencies required (uses stdlib `urllib`)

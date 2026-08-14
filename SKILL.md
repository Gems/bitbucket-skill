---
name: bitbucket
description: >
  Interact with Bitbucket — create PRs, list PRs, view PR details, update a PR's title/description, approve, merge,
  decline, read comments, view and trigger pipelines, CI-gated merge (poll the PR pipeline and merge when green), code
  review with inline comments, manage environments and variables. Use when user says "/bitbucket pr",
  "/bitbucket create-pr", "/bitbucket list-prs", "/bitbucket approve-pr", "approve PR", "/bitbucket pipelines",
  "/bitbucket run-pipeline", "run the pipeline", "trigger a build", "/bitbucket pipeline 1333", asks what happened in a
  build / pipeline named by its number ("check build #1333"), "/bitbucket merge-when-green", "/bitbucket ship",
  "/bitbucket prettify-pr", "prettify PR", asks to open a PR and get it merged once CI passes / merge when the pipeline
  is green, asks to regenerate/clean up/fix a PR's title or description from its commits, or similar Bitbucket-related
  requests.
argument-hint: "[command] [args]"
allowed-tools: Bash(python3 *), Bash(*.claude/skills/bitbucket/scripts/*), Bash(*.agents/skills/bitbucket/scripts/*), Bash(*.codex/skills/bitbucket/scripts/*)
---

# Bitbucket Skill

Lightweight Bitbucket CLI for Claude Code. Uses a Python script that calls Bitbucket Cloud REST API 2.0 directly and returns **compact output** to minimize token usage.

For direct CLI commands (e.g. `/bitbucket create-pr 123`), run the script from
whichever location exists (see **Script Location** below) with `$ARGUMENTS`.
Do not forward agent procedures such as `prettify-pr`, `merge-when-green`, or
`ship` as CLI commands; follow their documented workflows instead.

## Script Location

The script may live in any of these places — **look in all of them and use the
one that exists; if multiple exist, prefer a repo-attached copy**:

1. Repo-attached (preferred): `<repo>/.claude/skills/bitbucket/bitbucket_api.py`,
   `<repo>/.agents/skills/bitbucket/bitbucket_api.py`, or
   `<repo>/.codex/skills/bitbucket/bitbucket_api.py`
2. User-level: `~/.claude/skills/bitbucket/bitbucket_api.py`,
   `~/.agents/skills/bitbucket/bitbucket_api.py`, or
   `~/.codex/skills/bitbucket/bitbucket_api.py`

Set `BITBUCKET_SKILL` to the resolved `bitbucket` directory, then invoke
`python3 "$BITBUCKET_SKILL/bitbucket_api.py" <command> [args]`.

**Never `cd` into the script's directory before running it.** Workspace/repo
auto-detection reads the git remote of the *current working directory* — and
the repo-attached path above is commonly a symlink to this shared skill repo
itself (a personal skill install shared across projects), which is an
unrelated git repo. `cd`-ing there and then running the script resolves cwd
through the symlink and detects the wrong repo (or none at all). Always keep
cwd at the target project's root and invoke the script by its absolute path.

## Config

The config is derived from the resolved script location, independent of which
agent directory contains the skill:

1. Preferred: `bitbucket.config` next to `bitbucket_api.py`
2. Backward-compatible: `bitbucket.config` at the agent root containing the
   skill (for example `~/.codex/bitbucket.config` or
   `<repo>/.agents/bitbucket.config`)

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
python3 "$BITBUCKET_SKILL/bitbucket_api.py" <command> [args]
```

**Argument validation** — every command checks its own arguments and exits with
a specific error before any API call. Unknown flags, a flag missing its value
(or given the next flag as its value), extra positionals, a non-numeric PR ID,
an unknown merge strategy, and an out-of-range `COUNT` are all rejected;
nothing is silently ignored, so a typo can never produce a PR with a missing
description or a merge with the wrong strategy. If a command errors on its
arguments, fix the invocation — do not work around it with a raw `curl`. Run
with no arguments (or `help`) for usage.

### Create Pull Request

**Trigger**: `/bitbucket create-pr`, `/bitbucket pr`, "create PR", "open pull request"

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" create-pr "fix: IB-123: fix customer grid" --description "Summary of changes" --destination master
```

- `--description TEXT` — PR description (markdown), or use `--description-file PATH`
  for longer content (write it to a temp file first — a full description is
  typically too long/multiline for a single `--description` argument)
- `--source BRANCH` — source branch (default: current branch)
- `--destination BRANCH` — target branch (default: master)
- `--no-close` — don't close source branch after merge
- Returns: PR number and URL

The title must be a single quoted argument: `create-pr fix: IB-123: fix grid`
is rejected rather than silently truncated to `fix:`.

### List Pull Requests

**Trigger**: `/bitbucket list-prs`, `/bitbucket prs`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" list-prs [STATE] [--source BRANCH | --current-branch]
```

- STATE: `OPEN` (default), `MERGED`, `DECLINED`, `SUPERSEDED`
- `--source BRANCH` filters across every result page by the exact source branch
- `--current-branch` applies the same exact filter using the current Git branch

### View Pull Request

**Trigger**: `/bitbucket get-pr <ID>`, `/bitbucket pr <ID>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" get-pr 123
```

### Pull Request Commits

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pr-commits 123
```

- Returns every non-merge commit and its full message in chronological order
- Uses the PR commits endpoint, so fork-based PRs do not require local remotes

### Merge Pull Request

**Trigger**: `/bitbucket merge-pr <ID>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" merge-pr 123 --strategy squash
```

- `--strategy`: `merge_commit` (default), `squash`, `fast_forward` — any other
  value is rejected locally instead of merging with the default strategy

### Approve Pull Request

**Trigger**: `/bitbucket approve-pr <ID>`, "approve PR <ID>", "approve this PR"

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" approve-pr 123
```

- Approves the PR as the account behind the configured credential — confirm that
  account is the reviewer the user means, not a shared token, before running it.
- Only approve when the user explicitly asks for it: an approval is visible to
  the team and counts toward the repo's merge checks. Never approve as a step of
  another procedure (e.g. to unblock `merge-when-green`).
- Prints the approving user and the resulting participant state.

### Decline Pull Request

**Trigger**: `/bitbucket decline-pr <ID>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" decline-pr 123
```

### PR Comments

**Trigger**: `/bitbucket pr-comments <ID>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pr-comments 123
```

### Update Pull Request

**Trigger**: `/bitbucket update-pr <ID>`, "update the PR title/description", "rename this PR"

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" update-pr 123 --title "fix: IB-101: stop day slide" --description-file /tmp/pr-description.md
```

- `--title TEXT` — new PR title
- `--description TEXT` — new PR description (markdown), or use `--description-file PATH` for longer content
- At least one of `--title`/`--description`/`--description-file` must be given
- `--description` and `--description-file` are mutually exclusive

### Prettify PR (regenerate title & description from its commits)

**Trigger**: `/bitbucket prettify-pr <ID>`, "prettify PR <ID>", "clean up the PR description", "fix the PR title and description", "update the PR title/description accordingly"

This is an **agent procedure**, not a `bitbucket_api.py prettify-pr` command.
Rebuild an existing PR's title and description so they describe **what the PR
brings to the destination branch**, using the commits only as evidence of what
changed — not as the structure of the description. Follow **PR Title Format**
and **PR Description Formatting** below, then apply with `update-pr`:

1. **Resolve the PR.** If an ID/URL is given, use it; otherwise run `list-prs OPEN --current-branch` and use its exact match. If it returns zero or multiple PRs, stop and ask the user which PR to update. Run `get-pr <ID>` to confirm its source and destination branches.
2. **Gather the commits.** Run `pr-commits <ID>`. It reads the PR's paginated commits endpoint, works for same-repository and fork-based PRs, returns full commit messages, and excludes merge commits.
3. **Reduce the commits to the net change.** Read them in order and work out the end state of the branch, not its history. Later commits supersede earlier ones: a rename, a reworked approach, or a fix to something introduced earlier in the same branch means only the final form is part of the change. Discard branch-internal scaffolding entirely (see **Excluding branch-internal churn**).
4. **Build the title** per **PR Title Format**, using the terminology of the branch's *end state*.
5. **Build the description** per **PR Description Formatting**, grouping the net change into topical sections, including the `## Other Changes` split described there.
6. **Apply it** with `update-pr <ID> --title TEXT --description-file PATH` (write the description to a temp file first — it's typically too long/multiline for a single `--description` argument).
7. Report the new title, and say briefly what was dropped as branch-internal churn and what moved to `## Other Changes`, if anything.

### Add Comment to PR

**Trigger**: `/bitbucket add-comment <ID> <text>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" add-comment 123 "LGTM! Ready to merge."
```

- Everything after the ID becomes the comment body (unquoted words are joined),
  so quote the text. `add-comment` takes no options — an option-shaped first
  word is treated as a typo rather than posted as a comment.

### Pipelines

**Trigger**: `/bitbucket pipelines`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pipelines [COUNT]
```

- COUNT: number of recent pipelines to show, 1–100 (default: 10). The listing
  is a single API page, so a larger number is rejected rather than quietly
  capped at 100.
- Each row carries both identifiers: `Build` (the `#1333` number Bitbucket
  shows in its UI) and `Id` (the short uuid). Either one names the run in
  every command below.

**Status interpretation** — the Status column is `STATE/RESULT-or-STAGE`:
- `COMPLETED/SUCCESSFUL`, `COMPLETED/FAILED` — terminal results.
- `IN_PROGRESS/PAUSED` (or `/HALTED`) — **not running**: the pipeline succeeded up to a manual trigger step and is waiting for a human. Its Duration is wall-clock since the pause, so do **not** report it as "stuck", "slow", or "still building". Read it as "built OK, awaiting manual trigger".
- `IN_PROGRESS` with no stage — genuinely executing.
- For per-step detail and **why** a pipeline failed, use `pipeline` or
  `pipeline-steps` below rather than hand-rolling API calls.

### View Pipeline Run (by build number)

**Trigger**: `/bitbucket pipeline <PIPELINE_ID>`, "what happened in build #1333", "check pipeline 1333", "status of that build", a pasted `.../pipelines/results/1333` URL

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pipeline 1333
```

Use this whenever a **build number** is what you have — from the user, from a
chat message, from a `pipelines` row, or from the tail of a Bitbucket
`/pipelines/results/<N>` URL. It is one call: no listing scan, no uuid lookup.

- `PIPELINE_ID` is the build number (`1333`) or a pipeline uuid.
- Write the number **without** `#`, or quote it (`'#1333'`): an unquoted `#`
  starts a shell comment and the argument never reaches the script.
- Prints status, branch, commit, PR number (for a PR run), custom-pipeline
  pattern, trigger, creator, created/finished times, duration, the run's web
  URL and its **full uuid** on the `Id:` line — then the same per-step table
  as `pipeline-steps`, including each step's structured failure reason.
- When a step failed it prints the `pipeline-log <uuid>` follow-up to read.
- Unknown number → `no pipeline found for build #N ...`. Don't retry with a
  raw `curl`; check the number.

### Naming a run: build number in, uuid onward

Every pipeline command (`pipeline`, `pipeline-steps`, `pipeline-log`) takes
the same `PIPELINE_ID`, but the three forms do **not** cost the same:

| Form | Example | Cost to resolve |
|------|---------|-----------------|
| Full uuid | `d4a04f6c-1111-2222-3333-444455556666` | **none** — it is the API path (braces and case optional) |
| Build number | `1333` | one direct request |
| Short id (8 chars) | `d4a04f6c` | scans the last 100 runs; older runs are unreachable |

So the working order is:

1. The user hands you a **build number** (or a `/pipelines/results/1333` URL) —
   run `pipeline 1333`.
2. Take the **full uuid** off that output's `Id:` line (`run-pipeline` prints
   the same uuid for a run it just started).
3. Use that uuid for every follow-up in the conversation —
   `pipeline-steps <uuid>`, `pipeline-log <uuid>` — it resolves for free.

Keep the uuid in mind for the rest of the conversation and stop re-sending the
number. Never re-derive an id by listing pipelines and eyeballing the table:
that is the one path that both costs a request and silently misses old runs.
The short `Id` column in the `pipelines` listing is for reading, not for
passing on — take that row's `Build` number instead.

### Run Pipeline (trigger a build)

**Trigger**: `/bitbucket run-pipeline`, "run the pipeline", "trigger a build", "run the deploy pipeline"

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" run-pipeline
python3 "$BITBUCKET_SKILL/bitbucket_api.py" run-pipeline --branch release/1.2 --custom deploy-to-production --variable ENV=prod
```

- `--branch BRANCH` — branch to run on (default: current branch)
- `--custom PATTERN` — run the named `pipelines.custom.<PATTERN>` definition.
  Without it, the branch's own definition runs (the same one a push triggers).
- `--variable KEY=VALUE` — repeatable; passed to the run as a pipeline
  variable. Values are sent unsecured, so never pass a secret this way — put
  it in a repository/deployment variable and reference it from the YAML.
- Prints the build number, state, full uuid, and result URL, then the
  `pipeline-steps <uuid>` command to follow it — keep that uuid for any
  further check on this run.

Only run this when the user asks for it. A pipeline consumes build minutes and
a custom pipeline is frequently a **deploy** — confirm the branch and pattern
with the user before triggering one that ships anything, and never trigger a
pipeline as an unrequested step of another procedure.

### Pipeline Steps (why a pipeline failed)

**Trigger**: `/bitbucket pipeline-steps <PIPELINE_ID>`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pipeline-steps d4a04f6c
```

- `PIPELINE_ID` accepts the build number (`1333`), a full/braced uuid, or the
  short id shown in the `pipelines` table (first 8 chars) — see the cost table
  above and prefer a uuid you already have.
- Lists each step's name/state/result **and** the structured failure reason
  (e.g. `Container 'build' exceeded memory limit.` for an OOM kill). Check
  this before diving into logs — infra-level failures (OOM, timeout) surface
  here directly and are easy to miss by only reading Maven/test output, which
  can look clean right up to the point the container gets killed.

### Pipeline Log

**Trigger**: `/bitbucket pipeline-log <PIPELINE_ID> [STEP_ID]`

```bash
python3 "$BITBUCKET_SKILL/bitbucket_api.py" pipeline-log d4a04f6c [--lines N] [--full]
```

- `PIPELINE_ID` accepts a build number or a short/full uuid, same as above.
  Pass the full uuid when a previous command already printed it.
- `STEP_ID` optional — defaults to the first `FAILED` step (falls back to the
  last step if none failed). Also accepts a short id from `pipeline-steps`.
- Defaults to the last 200 lines; use `--lines N` or `--full` for more.
- Handles the log endpoint's redirect to a pre-signed S3 URL correctly (that
  request must drop the Bitbucket auth header or S3 returns 400) — don't
  reimplement this with a raw `curl`/`urllib` call.

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

PR title **must** follow the format: `<type>: <ticket(s)>: <summary>`

1. **Type prefix** — one of `feat`, `fix`, `chore`, `docs`, `refactor`, chosen from what the commit messages indicate. Combine with `/` when the changes span more than one type, e.g. `fix/feat`.
2. **Ticket ID(s)** — the ticket ID if one exists and is relevant, or a comma-separated list of them, followed by a colon. e.g. `IB-101, IB-102:`. Omit this part if there is no relevant ticket.
3. **Summary** — a short phrase summarizing the changes.

Use the terminology of the branch's **final state**, not of superseded steps: if
a concept, step, class, or column was renamed during the branch (or the naming
was settled in discussion), the title must use the settled name. Prefer the
words actually used by the code and docs at the PR head over wording from
earlier commit subjects.

Examples:
- `fix: IB-101: stop day slide in LocalDateTime conversion`
- `fix/feat: IB-101, IB-102: anchor date conversion and add config toggle`
- `refactor: simplify checkpoint flow routing`

## PR Description Formatting

Bitbucket PR descriptions use **Markdown** format (not ADF like JIRA).

There must be **no `Co-Authored-By` mention anywhere in the PR description**.

There must be **no standalone "Test Coverage" / "Testing" section**. If tests
were added or changed, fold that into the relevant `## Details` (or
`## Other Changes`) topic instead of breaking it out on its own.

A PR description addresses a reviewer of the **destination branch**. It states
what that branch gains, in its final shape. It is not a changelog of the source
branch and not a narrative of how the change was arrived at.

### PR Description Template

```markdown
## Summary

- What the destination branch gains, one bullet per user- or reviewer-visible outcome

## Details

### <topic of the change>

What this part of the change does, in its final form.

### <another topic>

...

## Other Changes

Unrelated to the main purpose, but deliberately included in this change set:

### <topic>

...
```

### Details section

The `## Details` section follows `## Summary` and elaborates the net change,
organized **by topic**, not by commit:

- Group related work into `###` subsections named after what changed (e.g. a new
  capability, a schema change, the filtering behavior, docs and tests) — do not
  emit one subsection per commit, and do not use commit subjects as headings.
- Describe the **end state**: what the code does after the merge. Never write
  history ("first added X, then renamed it to Y") — write Y.
- Several commits touching one topic collapse into one subsection; one large
  commit touching several topics splits across them.
- Reformat prose properly for Markdown:
  - Remove the hard line breaks added only for terminal wrapping; join wrapped lines back into normal paragraphs.
  - Prettify lists into proper Markdown lists.
  - Wrap class names, code symbols, and other code mentions in backticks (`` ` ``).
- If a commit message contains a `Co-Authored-By` line, **ignore it** — do not carry it into the description.

### Excluding branch-internal churn

Commits that only exist because of how the branch was developed are **not
changes to the destination branch** and must not appear in the description at
all — not as subsections, not as bullets:

- Fixups of code introduced earlier in the same branch: missing imports, missing
  or changed test parameters, compile errors, formatting, typo fixes.
- Review churn and rework: renames, reshuffles, and approach changes internal to
  the branch. Only the final name/approach is described.
- Superseded approaches: if the branch first implemented the feature one way and
  later replaced it, describe only the replacement. Do not mention what it
  replaced unless the *destination branch* already contained that older
  behavior, in which case the replacement itself is the change worth stating.
- WIP/checkpoint commits, merges from the destination branch, and reverts paired
  with the commit they revert.

Rule of thumb: if the commit's effect is invisible in a diff of destination →
PR head, it is churn. Fold its content into the topic it belongs to, or drop it.

### Other Changes section

Some work in a change set is deliberately included but isn't part of the PR's main purpose (e.g. an unrelated tooling/config tweak riding along with a feature branch). Don't blend it into `## Details` — split it out:

- Judge each topic against the PR's main purpose (the feature/fix the title describes). If its *primary* content doesn't serve that purpose, it belongs here instead of `## Details`.
- Add a `## Other Changes` section **after** `## Details`, with a one-line lead-in (e.g. "Unrelated to the main purpose, but deliberately included in this change set:").
- Format subsections the same way as `## Details` — topical `###` headings, same reformatting rules.
- If everything serves the main purpose, omit this section entirely — don't add it empty.
- Something mostly on-purpose that carries one incidental line (e.g. an unrelated dependency bump) stays in `## Details`; only split out work whose primary content is unrelated.

## Code Review with Inline Comments

### Single Inline Comment

Use the `bb-comment.sh` script to add inline code review comments:

```bash
"$BITBUCKET_SKILL/scripts/bb-comment.sh" <pr-id> <file-path> <line-number> "comment text"
```

Example:
```bash
"$BITBUCKET_SKILL/scripts/bb-comment.sh" 42 app/code/Vendor/Module/Model/Example.php 45 "Use dependency injection here"
```

### Batch Code Review

Use `bb-review.sh` for batch review with multiple comments:

```bash
"$BITBUCKET_SKILL/scripts/bb-review.sh" <pr-id> <comments-file>
```

Comments file format (TSV — tab separated):
```
file_path<TAB>line_number<TAB>comment_text
```

Example:
```bash
echo -e 'src/Model.php\t45\tUse DI here\nsrc/Controller.php\t120\tAdd try/catch' > /tmp/comments.tsv
"$BITBUCKET_SKILL/scripts/bb-review.sh" 42 /tmp/comments.tsv
```

Scripts auto-detect credentials from the config associated with their own
installation path or environment variables (`BB_WORKSPACE`, `BB_REPO`,
`BB_USER`, `BB_APP_PASSWORD`).

## Direct API Reference

For advanced use cases not covered by the Python script, see [references/api_reference.md](references/api_reference.md).

## Notes

- Default destination branch: **master**
- Auto-detects workspace/repo from git remote if not in config
- Output is already formatted as markdown — display directly to user
- No external Python dependencies required (uses stdlib `urllib`)

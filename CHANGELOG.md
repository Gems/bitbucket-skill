# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `approve-pr <ID>`: approves a PR via `POST /pullrequests/{id}/approve` as the account behind the configured credential, printing the approving user and resulting participant state. SKILL.md documents it as explicit-request-only — an approval is team-visible and counts toward merge checks, so it is never a step of another procedure.
- `create-pr`: `--description-file PATH`, matching `update-pr`, so a full description no longer has to fit in a single shell argument.
- `run-pipeline [--branch BRANCH] [--custom PATTERN] [--variable KEY=VALUE]`: triggers a pipeline via `POST /pipelines/`, closing the read-only gap left by `pipelines`/`pipeline-steps`/`pipeline-log`. Defaults to the current branch and its own definition; `--custom` selects a `pipelines.custom.<PATTERN>` definition and `--variable` is repeatable. Prints the build number, state, short id, and result URL, plus the `pipeline-steps <id>` command to follow it. SKILL.md documents it as explicit-request-only, since a custom pipeline is frequently a deploy.

### Fixed

- Every command now validates its own arguments and exits with a specific error before any API call, instead of silently ignoring what it does not understand. Previously `create-pr --description-file x.md` dropped the flag and opened a PR with an empty description, `merge-pr 1 --strategy squash-all` merged with the default strategy, `pipelines 500` quietly returned one 100-row page, and a bare `get-pr` printed the usage block with no indication of what was wrong.
  - Unknown options, flags missing their value, and stray positional arguments (such as an unquoted `create-pr` title) are rejected everywhere.
  - PR IDs must be numeric, so a URL or branch name is caught locally rather than sent to the API.
  - A leading positional can no longer be filled by an option: `pipeline-log --full` and `update-pr --title T` now report the missing ID.
  - An option can no longer swallow the following flag as its value: `update-pr 1 --title --description x` used to set the title to `--description`. Comment text and descriptions starting with a markdown `---` rule are still accepted.
  - `--strategy` is checked against `merge_commit`/`squash`/`fast_forward`, `pipelines COUNT` against the API's 1–100 page cap, and `list-prs STATE` reports the valid states.
  - Unreadable `--description-file` paths report the OS error instead of raising a traceback; `--description` and `--description-file` together are rejected.
  - An unknown command prints `Error: unknown command: X` plus usage on stderr; `help`/`--help`/`-h` print usage and exit 0 without needing a config file.

## [0.0.6] - 2026-06-19

### Added

- `merge-when-green` (alias `ship`) agent procedure: resolves/creates the PR, identifies its pipeline run, derives an expected runtime from the most recent successful PR pipeline, waits the deduced remaining time, re-checks (looping until terminal), and merges only on `COMPLETED/SUCCESSFUL`. Deducible from phrasings like "open a PR and get it merged" / "merge when the pipeline is green".

## [0.0.5] - 2026-06-18

### Added

- `pipelines`: status column now includes `state.stage`, so a run halted at a manual trigger reads as `IN_PROGRESS/PAUSED` instead of a bare `IN_PROGRESS` (its duration is wall-clock since the pause, not runtime).
- Repo-first resolution for both the script and `bitbucket.config`: a repo-attached copy next to the script takes precedence over the global `~/.claude/` location.

### Changed

- SKILL.md: documented the dual-location, repo-first resolution for the script and config, plus pipeline status interpretation.

## [0.0.4] - 2026-03-19

### Changed

- Renamed `skill.md` to `SKILL.md` to match official Claude Code skill naming convention
- Added `argument-hint` frontmatter field for autocomplete hints
- Added `allowed-tools` frontmatter field to allow `Bash(python3 *)` and skill scripts without per-use approval
- Added `$ARGUMENTS` substitution for direct invocation via `/bitbucket <command>`

### Fixed

- Fixed stale config path reference: `~/.bitbucket-rest-cli-config.json` → `~/.claude/bitbucket.config` in code review scripts section

## [0.0.3] - 2026-03-18

### Changed

- Shell scripts (`bb-comment.sh`, `bb-review.sh`) now read credentials from `~/.claude/bitbucket.config` instead of `~/.bitbucket-rest-cli-config.json` — unified config with Python API client
- Shell scripts now also read `workspace` and `repo_slug` from config (previously only from git remote)
- Updated authentication docs: Atlassian API Token with scopes is now the recommended auth method
- Marked Bitbucket App Passwords as deprecated in README
- Clarified that classic Atlassian API Tokens (without scopes) do not work with Bitbucket Cloud API

## [0.0.2] - 2026-03-16

### Added

- `CHANGELOG.md` for tracking project changes

### Removed

- `BB_CLI_CONFIG.md` — removed unused bb-cli (PHP) configuration docs
- bb-cli alternative section from `SKILL.md` and `README.md` — the skill uses only the Python API client

## [0.0.1] - 2026-03-16

### Added

- `SKILL.md` — skill entry point with command definitions, triggers, and config setup
- `bitbucket_api.py` — Python CLI client for Bitbucket Cloud REST API 2.0 (stdlib only, no external dependencies)
  - Pull request management: create, list, view, merge, decline
  - PR comments: add general and inline comments
  - Pipeline listing with status, duration, and trigger info
  - Auto-detection of workspace/repo from `git remote`
  - Flexible auth: App Password, Repository Access Token, API Token
- `scripts/bb-comment.sh` — shell script for adding single inline PR comments
- `scripts/bb-review.sh` — shell script for batch code review with multiple inline comments via TSV file
- `references/api_reference.md` — direct Bitbucket REST API reference for advanced use cases
- `README.md` — installation, authentication, and usage documentation
- `.gitignore` to exclude IDE files (`.idea/`) and credential config files (`*.config`)
- `LICENSE` — MIT License (Serhii Koval, Zghraia Software)

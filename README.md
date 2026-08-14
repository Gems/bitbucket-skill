# Bitbucket Skill

A lightweight Bitbucket Cloud CLI skill for coding agents. Manage pull requests, pipelines, and code reviews directly from your terminal — no external dependencies required.

## Features

- **Pull Requests**: Create, list, view, approve, merge, decline PRs
- **Comments**: Add general and inline code review comments
- **Pipelines**: View recent pipeline runs and statuses
- **Batch Code Review**: Add multiple inline comments at once
- **Auto-detection**: Workspace and repo are detected from `git remote`

## Installation

1. Copy the `bitbucket` folder to a user-level or project-level skills
   directory. Supported agent roots are `.claude`, `.agents`, and `.codex`:

```bash
cp -r bitbucket ~/.codex/skills/bitbucket
# Or: ~/.agents, ~/.claude, ./.codex, ./.agents, or ./.claude
```

2. Create `bitbucket.config` beside `bitbucket_api.py` (preferred), or at the
   containing agent root for backward compatibility:

```json
{
  "username": "your_bitbucket_username",
  "app_password": "your_bitbucket_app_password"
}
```

> `workspace` and `repo_slug` are optional — auto-detected from `git remote get-url origin`.

3. Make scripts executable:

```bash
chmod +x ~/.codex/skills/bitbucket/scripts/*.sh
```

## Authentication

Supports three auth methods (in priority order):

| # | Method | Config Keys | Auth Type |
|---|--------|------------|-----------|
| 1 | Atlassian API Token with scopes | `username` + `app_password` | Basic |
| 2 | Repository Access Token | `access_token` | Bearer |
| 3 | App Password ⚠️ | `username` + `app_password` | Basic |

### Getting an Atlassian API Token with Scopes (Recommended)

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **Create API token with scopes**
3. Select Bitbucket scopes: `read:pullrequest:bitbucket`, `write:pullrequest:bitbucket`, `read:repository:bitbucket`, `read:pipeline:bitbucket` (and others as needed)

```json
{
  "username": "your_atlassian_email",
  "app_password": "your_atlassian_api_token"
}
```

> **Note**: Classic Atlassian API Tokens (created with "Create API token" — without scopes) do NOT work with Bitbucket Cloud API. You must use **"Create API token with scopes"** and select Bitbucket scopes.

### Getting a Repository Access Token

1. Go to **Repository settings** > **Access tokens** > **Create**
2. Grant scopes: **Repositories** (Read/Write), **Pull Requests** (Read/Write)

```json
{
  "access_token": "your_repository_access_token"
}
```

> Repository Access Tokens are scoped to a single repository and do not require a username.

### Getting an App Password (Deprecated)

> **⚠️ Deprecated**: Bitbucket App Passwords are [deprecated by Atlassian](https://community.atlassian.com/forums/Bitbucket-questions/Deprecating-Atlassian-account-password-for-Bitbucket-API-and-Git/ba-p/2819787). Use Atlassian API Tokens with scopes instead.

1. Go to **Bitbucket** > **Personal settings** > **App passwords**
2. Create with scopes: **Repositories** (Read/Write), **Pull Requests** (Read/Write)

```json
{
  "username": "your_bitbucket_username",
  "app_password": "your_bitbucket_app_password"
}
```

## Usage

Once installed, use the `/bitbucket` command in your coding agent:

```
/bitbucket create-pr     — Create a pull request
/bitbucket list-prs      — List open PRs
/bitbucket get-pr 123    — View PR details
/bitbucket approve-pr 123 — Approve a PR
/bitbucket merge-pr 123  — Merge a PR
/bitbucket pr-comments 123 — View PR comments
/bitbucket pipelines     — View recent pipelines
/bitbucket pipeline 1333 — View one run by build number (or uuid), with its steps
/bitbucket run-pipeline  — Trigger a pipeline on a branch
```

Or use natural language:

```
"Create a PR for the current branch"
"Show me open pull requests"
"Merge PR #42 with squash strategy"
"What happened in build 1333?"
```

## Requirements

- Python 3.6+ (no external packages — uses stdlib only)
- `jq` (for shell scripts, optional)
- Git (for auto-detection of workspace/repo)

## License

MIT License. See [LICENSE](LICENSE) for details.

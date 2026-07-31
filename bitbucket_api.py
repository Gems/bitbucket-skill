#!/usr/bin/env python3
"""Lightweight Bitbucket CLI for Claude Code. Uses Bitbucket Cloud REST API 2.0."""

import json
import sys
import os
import ssl
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import base64
from collections import deque


def _config_paths(script_path=None):
    """Return config paths derived from this script's installation path."""
    script_dir = os.path.dirname(os.path.abspath(script_path or __file__))
    paths = [os.path.join(script_dir, "bitbucket.config")]

    # Standard installs use <agent-root>/skills/bitbucket/bitbucket_api.py.
    # Also support the historical config at <agent-root>/bitbucket.config
    # without hard-coding .claude, .agents, or .codex.
    skills_dir = os.path.dirname(script_dir)
    if os.path.basename(skills_dir) == "skills":
        paths.append(os.path.join(os.path.dirname(skills_dir), "bitbucket.config"))
    return paths


CONFIG_PATHS = _config_paths()


def _config_path():
    return next((p for p in CONFIG_PATHS if os.path.exists(p)), None)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


SSL_CTX = _ssl_context()


def load_config():
    path = _config_path()
    if not path:
        print(f"Error: Config not found. Looked in: {', '.join(CONFIG_PATHS)}", file=sys.stderr)
        print("Create one of them with:", file=sys.stderr)
        print(json.dumps({
            "username": "your_bitbucket_username",
            "app_password": "your_bitbucket_app_password",
            "workspace": "your-workspace",
            "repo_slug": "your-repo"
        }, indent=2), file=sys.stderr)
        print("\nUses Bitbucket App Password with Basic Auth.", file=sys.stderr)
        print("Create: Bitbucket → Personal settings → App passwords", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def _auth_header(config):
    # Priority: access_token (Bearer) > app_password (Basic) > api_token (Bearer)
    if "access_token" in config:
        return f"Bearer {config['access_token']}"
    if "app_password" in config:
        creds = base64.b64encode(
            f"{config['username']}:{config['app_password']}".encode()
        ).decode()
        return f"Basic {creds}"
    if "api_token" in config:
        return f"Bearer {config['api_token']}"
    print("Error: No auth credentials found in config", file=sys.stderr)
    sys.exit(1)


def api_request(config, path, method="GET", data=None):
    workspace = config["workspace"]
    repo_slug = config["repo_slug"]
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": _auth_header(config),
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        print(f"Error {e.code}: {body_text[:500]}", file=sys.stderr)
        sys.exit(1)


def _git(*args):
    """Run git command and return stripped stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, timeout=10
    )
    return result.stdout.strip()


def _parse_remote(remote):
    # SSH: git@bitbucket.org:workspace/repo.git
    # HTTPS: https://bitbucket.org/workspace/repo.git
    if "bitbucket.org" in remote:
        parts = remote.replace(".git", "").split("bitbucket.org")[-1]
        parts = parts.lstrip(":/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None, None


def _detect_repo_info():
    """Detect workspace and repo_slug from the cwd's git remote.

    This is inherently cwd-dependent, and the skill directory itself is
    often a symlink to a shared, unrelated location (e.g. a personal
    skill install shared across repos) - `cd`-ing into it before running
    this script resolves cwd through the symlink and breaks detection.
    Always invoke with an absolute path while keeping cwd at the target
    repo. For a permanent fix, set `workspace`/`repo_slug` explicitly in
    bitbucket.config so this never runs at all.
    """
    remote = _git("remote", "get-url", "origin")
    return _parse_remote(remote)


# ─── Commands ────────────────────────────────────────

def cmd_create_pr(config, title, description="", source=None, destination="master",
                  close_source=True, reviewers=None):
    """Create a pull request."""
    if not source:
        source = _git("branch", "--show-current")
        if not source:
            print("Error: Could not determine current branch", file=sys.stderr)
            sys.exit(1)

    payload = {
        "title": title,
        "source": {"branch": {"name": source}},
        "destination": {"branch": {"name": destination}},
        "close_source_branch": close_source,
    }
    if description:
        payload["description"] = description
    if reviewers:
        payload["reviewers"] = [{"uuid": r} for r in reviewers]

    result = api_request(config, "/pullrequests", method="POST", data=payload)
    pr_id = result["id"]
    url = result["links"]["html"]["href"]
    title_out = result["title"]
    print(f"Created **PR #{pr_id}**: {title_out}")
    print(f"URL: {url}")
    return result


def _parse_list_prs_args(args):
    state = "OPEN"
    source_branch = None
    state_seen = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--source":
            if (
                source_branch is not None
                or index + 1 >= len(args)
                or args[index + 1].startswith("--")
            ):
                print(
                    "Error: --source requires one branch and cannot be combined "
                    "with --current-branch",
                    file=sys.stderr,
                )
                sys.exit(1)
            source_branch = args[index + 1]
            index += 2
        elif arg == "--current-branch":
            if source_branch is not None:
                print(
                    "Error: --current-branch cannot be combined with --source",
                    file=sys.stderr,
                )
                sys.exit(1)
            source_branch = _git("branch", "--show-current")
            if not source_branch:
                print("Error: could not determine the current branch", file=sys.stderr)
                sys.exit(1)
            index += 1
        elif arg.startswith("--"):
            print(f"Error: unknown list-prs option: {arg}", file=sys.stderr)
            sys.exit(1)
        elif not state_seen:
            state = arg.upper()
            state_seen = True
            index += 1
        else:
            print("Error: list-prs accepts at most one STATE", file=sys.stderr)
            sys.exit(1)

    valid_states = {"OPEN", "MERGED", "DECLINED", "SUPERSEDED"}
    if state not in valid_states:
        print(f"Error: invalid pull request state: {state}", file=sys.stderr)
        sys.exit(1)
    return state, source_branch


def cmd_list_prs(config, state="OPEN", source_branch=None):
    """List pull requests."""
    state = state.upper()
    prs = _paginated_values(config, f"/pullrequests?state={state}&pagelen=25")
    if source_branch is not None:
        prs = [
            pr for pr in prs
            if pr["source"]["branch"]["name"] == source_branch
        ]
    if not prs:
        suffix = (
            f" for source branch {source_branch!r}" if source_branch is not None else ""
        )
        print(f"No {state.lower()} pull requests found{suffix}.")
        return

    print(f"## {state.title()} Pull Requests\n")
    print(f"| # | Title | Author | Branch | Updated |")
    print(f"|---|-------|--------|--------|---------|")
    for pr in prs:
        pr_id = pr["id"]
        title = pr["title"][:60]
        author = pr.get("author", {}).get("display_name", "—")
        branch = pr["source"]["branch"]["name"]
        updated = pr["updated_on"][:10] if pr.get("updated_on") else "—"
        print(f"| {pr_id} | {title} | {author} | {branch} | {updated} |")


def cmd_get_pr(config, pr_id):
    """Get pull request details."""
    pr = api_request(config, f"/pullrequests/{pr_id}")
    source = pr["source"]["branch"]["name"]
    dest = pr["destination"]["branch"]["name"]
    author = pr.get("author", {}).get("display_name", "—")
    state = pr["state"]
    created = pr["created_on"][:10] if pr.get("created_on") else "—"
    updated = pr["updated_on"][:10] if pr.get("updated_on") else "—"
    url = pr["links"]["html"]["href"]
    desc = pr.get("description", "")[:500] or "No description"
    reviewers = ", ".join(
        r.get("display_name", "—") for r in pr.get("reviewers", [])
    ) or "—"

    print(f"## PR #{pr['id']}: {pr['title']}")
    print(f"State: {state} | Author: {author}")
    print(f"Branch: {source} → {dest}")
    print(f"Reviewers: {reviewers}")
    print(f"Created: {created} | Updated: {updated}")
    print(f"URL: {url}")
    print(f"\n{desc}")


def cmd_pr_commits(config, pr_id):
    """List the non-merge commits that belong to a pull request."""
    commits = _paginated_values(
        config, f"/pullrequests/{pr_id}/commits?pagelen=50"
    )
    commits = [commit for commit in commits if len(commit.get("parents", [])) <= 1]
    if not commits:
        print(f"No non-merge commits found for PR #{pr_id}.")
        return

    print(f"## Non-merge commits for PR #{pr_id}\n")
    for commit in commits:
        commit_hash = commit.get("hash", "—")
        message = (commit.get("message") or "No commit message").rstrip()
        print(f"### {commit_hash}")
        print(message)
        print("\n<<<END>>>\n")


def cmd_update_pr(config, pr_id, title=None, description=None):
    """Update a pull request's title and/or description."""
    payload = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description

    if not payload:
        print(
            "Error: provide at least one of --title, --description, --description-file",
            file=sys.stderr,
        )
        sys.exit(1)

    result = api_request(
        config, f"/pullrequests/{pr_id}", method="PUT", data=payload
    )
    print(f"Updated PR #{result['id']}: {result['title']}")
    if "description" in payload:
        desc_len = len(result.get("description") or "")
        print(f"Description length: {desc_len} chars")
    print(f"URL: {result['links']['html']['href']}")


def cmd_merge_pr(config, pr_id, strategy="merge_commit"):
    """Merge a pull request."""
    data = api_request(
        config,
        f"/pullrequests/{pr_id}/merge",
        method="POST",
        data={"merge_strategy": strategy, "close_source_branch": True}
    )
    print(f"Merged PR #{pr_id}: {data.get('title', '')}")
    print(f"Merge commit: {data.get('merge_commit', {}).get('hash', '—')[:12]}")


def cmd_approve_pr(config, pr_id):
    """Approve a pull request on behalf of the configured account."""
    data = api_request(
        config, f"/pullrequests/{pr_id}/approve", method="POST"
    )
    user = data.get("user", {}).get("display_name", "—")
    state = data.get("state") or ("approved" if data.get("approved") else "—")
    print(f"Approved PR #{pr_id} as {user} (state: {state})")


def cmd_decline_pr(config, pr_id):
    """Decline a pull request."""
    data = api_request(config, f"/pullrequests/{pr_id}/decline", method="POST")
    print(f"Declined PR #{pr_id}: {data.get('title', '')}")


def cmd_pr_comments(config, pr_id):
    """List PR comments."""
    data = api_request(config, f"/pullrequests/{pr_id}/comments?pagelen=50")
    comments = data.get("values", [])
    if not comments:
        print("No comments.")
        return

    print(f"## Comments on PR #{pr_id}\n")
    for c in comments:
        author = c.get("user", {}).get("display_name", "—")
        created = c.get("created_on", "")[:16].replace("T", " ")
        body = c.get("content", {}).get("raw", "")[:400]
        inline = c.get("inline")
        location = ""
        if inline:
            path = inline.get("path", "")
            line = inline.get("to") or inline.get("from") or ""
            location = f" (`{path}:{line}`)"
        print(f"**{author}** — {created}{location}")
        print(f"{body}\n")


def cmd_add_comment(config, pr_id, text):
    """Add a comment to a PR."""
    data = api_request(
        config,
        f"/pullrequests/{pr_id}/comments",
        method="POST",
        data={"content": {"raw": text}}
    )
    print(f"Comment added to PR #{pr_id}")


def _resolve_pipeline_uuid(config, short_id):
    """Resolve a full or short/truncated pipeline uuid (with or without
    braces) to the full `{uuid}` form the API expects."""
    needle = short_id.strip("{}").lower()
    if len(needle) == 36:  # already a full uuid
        return f"{{{needle}}}"
    data = api_request(config, "/pipelines/?pagelen=50&sort=-created_on")
    for p in data.get("values", []):
        if p.get("uuid", "").strip("{}").lower().startswith(needle):
            return p["uuid"]
    print(f"Error: no pipeline found matching '{short_id}' in the last 50 runs", file=sys.stderr)
    sys.exit(1)


def _resolve_step_uuid(steps, short_id):
    needle = short_id.strip("{}").lower()
    for s in steps:
        if s.get("uuid", "").strip("{}").lower().startswith(needle):
            return s
    print(f"Error: no step found matching '{short_id}'", file=sys.stderr)
    sys.exit(1)


def _pipeline_step_error(state_obj):
    return state_obj.get("result", {}).get("error", {}).get("message")


def _relative_api_path(config, url):
    """Convert a Bitbucket pagination URL back to a repository-relative path."""
    parsed = urllib.parse.urlsplit(url)
    prefix = (f"/2.0/repositories/{config['workspace']}/"
              f"{config['repo_slug']}")
    if (parsed.scheme != "https" or parsed.netloc != "api.bitbucket.org"
            or not parsed.path.startswith(prefix)):
        print(f"Error: unexpected Bitbucket pagination URL: {url}", file=sys.stderr)
        sys.exit(1)
    path = parsed.path[len(prefix):] or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return path


def _paginated_values(config, path):
    """Fetch every page from a Bitbucket repository collection endpoint."""
    values = []
    while path:
        data = api_request(config, path)
        values.extend(data.get("values", []))
        next_url = data.get("next")
        path = _relative_api_path(config, next_url) if next_url else None
    return values


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return redirect responses without following their Location header."""

    def http_error_302(self, request, response, code, message, headers):
        return response

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def cmd_pipeline_steps(config, pipeline_id):
    """List steps for a pipeline run, including the structured failure
    reason (e.g. memory-limit-exceeded) that log-scraping would miss."""
    pipeline_uuid = _resolve_pipeline_uuid(config, pipeline_id)
    steps = _paginated_values(
        config, f"/pipelines/{pipeline_uuid}/steps/?pagelen=100"
    )
    if not steps:
        print("No steps found.")
        return

    print(f"## Steps for pipeline {pipeline_uuid}\n")
    print("| # | Name | State | Result | Error |")
    print("|---|------|-------|--------|-------|")
    for s in steps:
        uuid = s.get("uuid", "—")[1:9]
        name = s.get("name", "—")
        state_obj = s.get("state", {})
        state = state_obj.get("name", "—")
        result = state_obj.get("result", {}).get("name", "—")
        error = _pipeline_step_error(state_obj) or ""
        print(f"| {uuid} | {name} | {state} | {result} | {error} |")


def _read_log_lines(response, lines):
    """Decode a log stream while retaining only the requested tail."""
    body_lines = [] if lines is None else deque(maxlen=lines)
    total_lines = 0
    for raw_line in response:
        total_lines += 1
        body_lines.append(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
    return total_lines, list(body_lines)


def _pipeline_log_error(error):
    if isinstance(error, urllib.error.HTTPError):
        body = error.read().decode("utf-8", errors="replace") if error.fp else ""
        detail = f"Error {error.code}: {body[:500]}"
    else:
        detail = f"Error downloading pipeline log: {error.reason}"
    print(detail, file=sys.stderr)
    sys.exit(1)


def _fetch_pipeline_log(config, pipeline_uuid, step_uuid, lines):
    url = (f"https://api.bitbucket.org/2.0/repositories/{config['workspace']}/"
           f"{config['repo_slug']}/pipelines/{pipeline_uuid}/steps/{step_uuid}/log")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=SSL_CTX),
        _NoRedirect,
    )
    request = urllib.request.Request(
        url, headers={"Authorization": _auth_header(config)}
    )
    try:
        with opener.open(request, timeout=30) as response:
            if response.status not in (301, 302, 303, 307, 308):
                return _read_log_lines(response, lines)

            location = response.headers.get("Location")
            if not location:
                print("Error: pipeline log redirect omitted Location", file=sys.stderr)
                sys.exit(1)

        # Deliberately create a fresh request with no Authorization header.
        download_request = urllib.request.Request(location)
        with urllib.request.urlopen(
                download_request, context=SSL_CTX, timeout=60) as response:
            return _read_log_lines(response, lines)
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        _pipeline_log_error(error)


def cmd_pipeline_log(config, pipeline_id, step_id=None, lines=200):
    """Fetch the log for a pipeline step (defaults to the first FAILED
    step, or the last step if none failed).

    The log endpoint 307-redirects to a pre-signed S3 URL; that request
    must NOT carry the Bitbucket Authorization header or S3 rejects it
    with 400. We follow the redirect manually to drop it.
    """
    pipeline_uuid = _resolve_pipeline_uuid(config, pipeline_id)
    if lines is not None and lines <= 0:
        print("Error: --lines must be a positive integer", file=sys.stderr)
        sys.exit(1)

    steps = _paginated_values(
        config, f"/pipelines/{pipeline_uuid}/steps/?pagelen=100"
    )
    if not steps:
        print("No steps found.", file=sys.stderr)
        sys.exit(1)

    if step_id:
        step = _resolve_step_uuid(steps, step_id)
    else:
        step = next(
            (s for s in steps if s.get("state", {}).get("result", {}).get("name") == "FAILED"),
            steps[-1],
        )

    step_uuid = step["uuid"]
    total_lines, body_lines = _fetch_pipeline_log(
        config, pipeline_uuid, step_uuid, lines
    )

    print(f"## Log for step '{step.get('name', '—')}' ({step_uuid[1:9]}) — "
          f"{total_lines} lines total\n")
    if lines and total_lines > lines:
        print(f"...(showing last {lines} lines; pass a bigger --lines to see more)...\n")
    print("\n".join(body_lines))


def _parse_pipeline_log_args(args):
    step_id = None
    lines = 200
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--lines":
            if index + 1 >= len(args):
                print("Error: --lines requires a positive integer", file=sys.stderr)
                sys.exit(1)
            try:
                lines = int(args[index + 1])
            except ValueError:
                print("Error: --lines requires a positive integer", file=sys.stderr)
                sys.exit(1)
            if lines <= 0:
                print("Error: --lines must be a positive integer", file=sys.stderr)
                sys.exit(1)
            index += 2
        elif arg == "--full":
            lines = None
            index += 1
        elif arg.startswith("--"):
            print(f"Error: unknown pipeline-log option: {arg}", file=sys.stderr)
            sys.exit(1)
        elif step_id is None:
            step_id = arg
            index += 1
        else:
            print("Error: pipeline-log accepts at most one STEP_ID", file=sys.stderr)
            sys.exit(1)
    return step_id, lines


def cmd_pipelines(config, count=10):
    """List recent pipeline runs."""
    data = api_request(config, f"/pipelines/?pagelen={count}&sort=-created_on")
    pipelines = data.get("values", [])
    if not pipelines:
        print("No pipelines found.")
        return

    print("## Recent Pipelines\n")
    print("| # | Branch | Status | Duration | Trigger | Created |")
    print("|---|--------|--------|----------|---------|---------|")
    for p in pipelines:
        uuid = p.get("uuid", "—")[:8]
        target = p.get("target", {})
        branch = target.get("ref_name", target.get("selector", {}).get("pattern", "—"))
        state_obj = p.get("state", {})
        state = state_obj.get("name", "—")
        result = state_obj.get("result", {}).get("name", "")
        # IN_PROGRESS pipelines carry a stage (e.g. PAUSED/HALTED) when waiting
        # on a manual trigger step — surface it so paused != actively running.
        stage = state_obj.get("stage", {}).get("name", "")
        status = f"{state}/{result or stage}" if (result or stage) else state
        duration = p.get("duration_in_seconds")
        dur_str = f"{duration // 60}m {duration % 60}s" if duration else "—"
        trigger = target.get("type", "—").replace("pipeline_ref_target", "push")
        created = p.get("created_on", "")[:16].replace("T", " ")
        print(f"| {uuid} | {branch} | {status} | {dur_str} | {trigger} | {created} |")


USAGE = """Usage: bitbucket_api.py <command> [args]

Commands:
  create-pr <TITLE> [--description TEXT] [--source BRANCH] [--destination BRANCH] [--no-close]
                                     Create pull request
  list-prs [STATE] [--source BRANCH | --current-branch]
                                     List PRs (OPEN/MERGED/DECLINED/SUPERSEDED)
  get-pr <ID>                        View PR details
  pr-commits <ID>                    List a PR's non-merge commits and full messages
  update-pr <ID> [--title TEXT] [--description TEXT] [--description-file PATH]
                                     Update PR title and/or description
  merge-pr <ID> [--strategy S]       Merge PR (merge_commit/squash/fast_forward)
  approve-pr <ID>                    Approve PR as the configured account
  decline-pr <ID>                    Decline PR
  pr-comments <ID>                   List PR comments
  add-comment <ID> <TEXT>            Add comment to PR
  pipelines [COUNT]                  List recent pipelines
  pipeline-steps <PIPELINE_ID>       List steps + failure reason for a pipeline (full or short uuid)
  pipeline-log <PIPELINE_ID> [STEP_ID] [--lines N] [--full]
                                     Fetch a step's log (defaults to the first FAILED step, last N lines)"""


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    config = load_config()

    # Auto-detect workspace/repo from git remote if not in config
    if "workspace" not in config or "repo_slug" not in config:
        ws, repo = _detect_repo_info()
        if ws and repo:
            config.setdefault("workspace", ws)
            config.setdefault("repo_slug", repo)
        else:
            print(
                "Error: Could not detect workspace/repo from the current directory's "
                "git remote.\nIf you `cd`-ed into this script's own directory before "
                "running it, note it may be a symlink to a shared location unrelated "
                "to your target repo - re-run with cwd at the target repo's root "
                "instead (invoke the script by absolute path, don't cd to it).\n"
                "Alternatively, add \"workspace\"/\"repo_slug\" to "
                f"{_config_path()} (note: that config may be shared across repos).",
                file=sys.stderr,
            )
            sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "create-pr" and len(sys.argv) >= 3:
        title = sys.argv[2]
        description = ""
        source = None
        destination = "master"
        close_source = True
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--description" and i + 1 < len(sys.argv):
                description = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--destination" and i + 1 < len(sys.argv):
                destination = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--no-close":
                close_source = False
                i += 1
            else:
                i += 1
        cmd_create_pr(config, title, description, source, destination, close_source)

    elif cmd == "list-prs":
        state, source_branch = _parse_list_prs_args(sys.argv[2:])
        cmd_list_prs(config, state, source_branch=source_branch)

    elif cmd == "get-pr" and len(sys.argv) >= 3:
        cmd_get_pr(config, sys.argv[2])

    elif cmd == "pr-commits" and len(sys.argv) >= 3:
        cmd_pr_commits(config, sys.argv[2])

    elif cmd == "update-pr" and len(sys.argv) >= 3:
        pr_id = sys.argv[2]
        title = None
        description = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--title" and i + 1 < len(sys.argv):
                title = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--description" and i + 1 < len(sys.argv):
                description = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--description-file" and i + 1 < len(sys.argv):
                with open(sys.argv[i + 1]) as f:
                    description = f.read()
                i += 2
            else:
                i += 1
        cmd_update_pr(config, pr_id, title=title, description=description)

    elif cmd == "merge-pr" and len(sys.argv) >= 3:
        strategy = "merge_commit"
        if "--strategy" in sys.argv:
            idx = sys.argv.index("--strategy")
            if idx + 1 < len(sys.argv):
                strategy = sys.argv[idx + 1]
        cmd_merge_pr(config, sys.argv[2], strategy)

    elif cmd == "approve-pr" and len(sys.argv) >= 3:
        cmd_approve_pr(config, sys.argv[2])

    elif cmd == "decline-pr" and len(sys.argv) >= 3:
        cmd_decline_pr(config, sys.argv[2])

    elif cmd == "pr-comments" and len(sys.argv) >= 3:
        cmd_pr_comments(config, sys.argv[2])

    elif cmd == "add-comment" and len(sys.argv) >= 4:
        text = " ".join(sys.argv[3:])
        cmd_add_comment(config, sys.argv[2], text)

    elif cmd == "pipelines":
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        cmd_pipelines(config, count)

    elif cmd == "pipeline-steps" and len(sys.argv) >= 3:
        cmd_pipeline_steps(config, sys.argv[2])

    elif cmd == "pipeline-log" and len(sys.argv) >= 3:
        pipeline_id = sys.argv[2]
        step_id, lines = _parse_pipeline_log_args(sys.argv[3:])
        cmd_pipeline_log(config, pipeline_id, step_id, lines)

    else:
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()

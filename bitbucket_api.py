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
# Config is resolved repo-first: a bitbucket.config next to this script (the
# repo-attached copy) wins over the global ~/.claude/bitbucket.config.
CONFIG_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bitbucket.config"),
    os.path.expanduser("~/.claude/bitbucket.config"),
]


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


def _detect_repo_info():
    """Detect workspace and repo_slug from git remote."""
    remote = _git("remote", "get-url", "origin")
    # SSH: git@bitbucket.org:workspace/repo.git
    # HTTPS: https://bitbucket.org/workspace/repo.git
    if "bitbucket.org" in remote:
        parts = remote.replace(".git", "").split("bitbucket.org")[-1]
        parts = parts.lstrip(":/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None, None


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


def cmd_list_prs(config, state="OPEN"):
    """List pull requests."""
    state = state.upper()
    data = api_request(config, f"/pullrequests?state={state}&pagelen=25")
    prs = data.get("values", [])
    if not prs:
        print(f"No {state.lower()} pull requests found.")
        return

    print(f"## {state.title()} Pull Requests\n")
    print(f"| # | Title | Author | Branch | Updated |")
    print(f"|---|-------|--------|--------|---------|")
    for pr in prs:
        pr_id = pr["id"]
        title = pr["title"][:60]
        author = pr.get("author", {}).get("display_name", "—")
        branch = pr["source"]["branch"]["name"][:40]
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
  list-prs [STATE]                   List PRs (OPEN/MERGED/DECLINED/SUPERSEDED)
  get-pr <ID>                        View PR details
  update-pr <ID> [--title TEXT] [--description TEXT] [--description-file PATH]
                                     Update PR title and/or description
  merge-pr <ID> [--strategy S]       Merge PR (merge_commit/squash/fast_forward)
  decline-pr <ID>                    Decline PR
  pr-comments <ID>                   List PR comments
  add-comment <ID> <TEXT>            Add comment to PR
  pipelines [COUNT]                  List recent pipelines"""


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
            print("Error: Could not detect workspace/repo. Add to config.", file=sys.stderr)
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
        state = sys.argv[2] if len(sys.argv) > 2 else "OPEN"
        cmd_list_prs(config, state)

    elif cmd == "get-pr" and len(sys.argv) >= 3:
        cmd_get_pr(config, sys.argv[2])

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

    else:
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()

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


def api_request(config, path, method="GET", data=None, allow_404=False):
    """Call the API, exiting on any error. With allow_404, a missing resource
    returns None instead so the caller can try another lookup."""
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
        if allow_404 and e.code == 404:
            return None
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


# ─── Argument validation ─────────────────────────────
#
# Every command parses its own argv tail through these helpers and exits on
# anything it does not understand. Silently skipping an unrecognised argument
# is never acceptable here: the commands write to a shared PR, so a dropped
# flag ships a wrong title, an empty description, or the wrong merge strategy.

def _fail(message):
    """Report a usage error on stderr and exit non-zero."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _looks_like_option(arg):
    return arg.startswith("--")


def _looks_like_flag_name(arg):
    """`--word`-shaped, unlike a markdown `---` rule that opens a comment."""
    return arg.startswith("--") and arg[2:3].isalpha()


def _option_value(args, index, what):
    """Return the value following the option at args[index].

    A flag-shaped value means the option swallowed the next flag
    (`update-pr 1 --title --description x`), which would otherwise ship
    `--description` as the new title.
    """
    flag = args[index]
    if index + 1 >= len(args):
        _fail(f"{flag} requires {what}")
    value = args[index + 1]
    if _looks_like_flag_name(value):
        _fail(f"{flag} requires {what}, got the option {value}")
    return value


def _leading_positional(command, args, name):
    """Split a command's required leading positional off from its options.

    Options are rejected in that slot, so a flag that swallowed its value
    (`get-pr --title x`) fails instead of being sent as an ID.
    """
    if not args:
        _fail(f"{command} requires a {name}")
    if _looks_like_option(args[0]):
        _fail(f"{command} requires a {name} before its options, got {args[0]}")
    return args[0], args[1:]


def _single_positional(command, args, name):
    """Parse a command that takes exactly one positional and no options."""
    value, rest = _leading_positional(command, args, name)
    if rest:
        _fail(
            f"{command} accepts exactly one {name}, "
            f"got extra argument: {rest[0]!r}"
        )
    return value


def _validate_pr_id(command, value):
    if not value.isdigit() or int(value) < 1:
        _fail(
            f"{command} requires a numeric PR ID (not a URL or branch), "
            f"got {value!r}"
        )
    return value


def _single_pr_id(command, args):
    return _validate_pr_id(command, _single_positional(command, args, "PR ID"))


# ─── Commands ────────────────────────────────────────

def _parse_description_flag(command, args, index, already_set):
    """Resolve --description/--description-file at args[index] to its text.

    Exits when the flag is repeated, combined with its sibling, missing its
    value, or points at a file that cannot be read.
    """
    flag = args[index]
    if already_set:
        _fail(f"{command} accepts only one of --description/--description-file")
    value = _option_value(args, index, "a value")
    if flag == "--description":
        return value
    try:
        with open(value, encoding="utf-8") as handle:
            return handle.read()
    except OSError as error:
        _fail(f"could not read {value}: {error.strerror or error}")


def _parse_create_pr_args(args):
    title, rest = _leading_positional("create-pr", args, "TITLE")
    if not title.strip():
        _fail("create-pr requires a non-empty TITLE")
    description = None
    source = None
    destination = "master"
    close_source = True
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in ("--description", "--description-file"):
            description = _parse_description_flag(
                "create-pr", rest, index, description is not None
            )
            index += 2
        elif arg in ("--source", "--destination"):
            branch = _option_value(rest, index, "a branch")
            if arg == "--source":
                source = branch
            else:
                destination = branch
            index += 2
        elif arg == "--no-close":
            close_source = False
            index += 1
        elif _looks_like_option(arg):
            _fail(f"unknown create-pr option: {arg}")
        else:
            _fail(
                f"unexpected create-pr argument: {arg!r} "
                "(the title must be a single quoted argument)"
            )
    return title, description or "", source, destination, close_source


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
                or _looks_like_option(args[index + 1])
            ):
                _fail(
                    "--source requires one branch and cannot be combined "
                    "with --current-branch"
                )
            source_branch = args[index + 1]
            index += 2
        elif arg == "--current-branch":
            if source_branch is not None:
                _fail("--current-branch cannot be combined with --source")
            source_branch = _git("branch", "--show-current")
            if not source_branch:
                _fail("could not determine the current branch")
            index += 1
        elif _looks_like_option(arg):
            _fail(f"unknown list-prs option: {arg}")
        elif not state_seen:
            state = arg.upper()
            state_seen = True
            index += 1
        else:
            _fail("list-prs accepts at most one STATE")

    valid_states = {"OPEN", "MERGED", "DECLINED", "SUPERSEDED"}
    if state not in valid_states:
        _fail(
            f"invalid pull request state: {state} "
            f"(expected one of: {', '.join(sorted(valid_states))})"
        )
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


def _parse_update_pr_args(args):
    pr_id, rest = _leading_positional("update-pr", args, "PR ID")
    _validate_pr_id("update-pr", pr_id)
    title = None
    description = None
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in ("--description", "--description-file"):
            description = _parse_description_flag(
                "update-pr", rest, index, description is not None
            )
            index += 2
        elif arg == "--title":
            title = _option_value(rest, index, "a value")
            if not title.strip():
                _fail("--title requires a non-empty value")
            index += 2
        elif _looks_like_option(arg):
            _fail(f"unknown update-pr option: {arg}")
        else:
            _fail(f"unexpected update-pr argument: {arg!r}")
    if title is None and description is None:
        _fail("provide at least one of --title, --description, --description-file")
    return pr_id, title, description


def cmd_update_pr(config, pr_id, title=None, description=None):
    """Update a pull request's title and/or description."""
    payload = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description

    if not payload:
        _fail("provide at least one of --title, --description, --description-file")

    result = api_request(
        config, f"/pullrequests/{pr_id}", method="PUT", data=payload
    )
    print(f"Updated PR #{result['id']}: {result['title']}")
    if "description" in payload:
        desc_len = len(result.get("description") or "")
        print(f"Description length: {desc_len} chars")
    print(f"URL: {result['links']['html']['href']}")


MERGE_STRATEGIES = ("merge_commit", "squash", "fast_forward")


def _parse_merge_pr_args(args):
    pr_id, rest = _leading_positional("merge-pr", args, "PR ID")
    _validate_pr_id("merge-pr", pr_id)
    strategy = "merge_commit"
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--strategy":
            strategy = _option_value(
                rest, index, f"one of: {', '.join(MERGE_STRATEGIES)}"
            )
            if strategy not in MERGE_STRATEGIES:
                _fail(
                    f"unknown merge strategy: {strategy} "
                    f"(expected one of: {', '.join(MERGE_STRATEGIES)})"
                )
            index += 2
        elif _looks_like_option(arg):
            _fail(f"unknown merge-pr option: {arg}")
        else:
            _fail(f"unexpected merge-pr argument: {arg!r}")
    return pr_id, strategy


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


def _parse_add_comment_args(args):
    pr_id, rest = _leading_positional("add-comment", args, "PR ID")
    _validate_pr_id("add-comment", pr_id)
    if not rest:
        _fail("add-comment requires comment text")
    # The remaining words are joined into the comment, so nothing can be
    # dropped - but add-comment has no options, so an option-shaped first
    # word is a typo rather than prose. A markdown `---` rule still passes.
    if _looks_like_flag_name(rest[0]):
        _fail(
            f"unknown add-comment option: {rest[0]} "
            "(usage: add-comment <ID> <TEXT>)"
        )
    text = " ".join(rest)
    if not text.strip():
        _fail("add-comment requires non-empty comment text")
    return pr_id, text


def cmd_add_comment(config, pr_id, text):
    """Add a comment to a PR."""
    data = api_request(
        config,
        f"/pullrequests/{pr_id}/comments",
        method="POST",
        data={"content": {"raw": text}}
    )
    print(f"Comment added to PR #{pr_id}")


# Bitbucket caps a page at 100 results, and the pipeline listing is not
# paginated - a larger COUNT would silently return 100 rows, so reject it up
# front. The same cap bounds how far back an id lookup can search.
MAX_PIPELINE_COUNT = 100


# A run has two names: the `#1333` build number shown in the UI, in
# `run-pipeline` output and in chat, and the uuid the API returns. Every
# command that names a run accepts both, so a build number never has to be
# translated to a uuid by hand.

def _pipeline_build_number(pipeline_id):
    """Return the build number a pipeline id names, or None for a uuid."""
    needle = pipeline_id.strip().lstrip("#")
    return needle if needle.isdigit() and int(needle) > 0 else None


def _pipeline_uuid_needle(pipeline_id):
    """Normalise a pipeline id to a bare lowercase uuid (prefix)."""
    return pipeline_id.strip().lstrip("#").strip("{}").lower()


def _recent_pipelines(config):
    data = api_request(
        config, f"/pipelines/?pagelen={MAX_PIPELINE_COUNT}&sort=-created_on"
    )
    return data.get("values", [])


def _fetch_pipeline(config, pipeline_id):
    """Resolve a build number (`#1333`/`1333`) or a full/truncated uuid to the
    pipeline object it names."""
    build_number = _pipeline_build_number(pipeline_id)
    needle = _pipeline_uuid_needle(pipeline_id)
    if not needle:
        _fail(f"invalid pipeline id: {pipeline_id!r}")

    if build_number:
        # Bitbucket resolves a build number on the same path as a uuid. Verify
        # the number came back as asked, and keep the listing fallback: an
        # all-digit value can also be the prefix of a uuid.
        pipeline = api_request(config, f"/pipelines/{build_number}", allow_404=True)
        if pipeline and str(pipeline.get("build_number")) == build_number:
            return pipeline
    elif len(needle) == 36:  # already a full uuid
        return api_request(config, f"/pipelines/{{{needle}}}")

    recent = _recent_pipelines(config)
    if build_number:
        for pipeline in recent:
            if str(pipeline.get("build_number")) == build_number:
                return pipeline
    for pipeline in recent:
        if pipeline.get("uuid", "").strip("{}").lower().startswith(needle):
            return pipeline

    named = f"build #{build_number} or uuid" if build_number else "uuid"
    print(
        f"Error: no pipeline found for {named} '{needle}' "
        f"(searched the last {MAX_PIPELINE_COUNT} runs)",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_pipeline_uuid(config, pipeline_id):
    """Resolve a build number, or a full/truncated uuid (with or without
    braces), to the full `{uuid}` form the API expects."""
    needle = _pipeline_uuid_needle(pipeline_id)
    if not _pipeline_build_number(pipeline_id) and len(needle) == 36:
        return f"{{{needle}}}"  # already a full uuid - no lookup needed
    return _fetch_pipeline(config, pipeline_id)["uuid"]


def _resolve_step_uuid(steps, short_id):
    needle = short_id.strip("{}").lower()
    for s in steps:
        if s.get("uuid", "").strip("{}").lower().startswith(needle):
            return s
    print(f"Error: no step found matching '{short_id}'", file=sys.stderr)
    sys.exit(1)


def _pipeline_step_error(state_obj):
    return state_obj.get("result", {}).get("error", {}).get("message")


def _pipeline_status(state_obj):
    """Render a run's state as `STATE/RESULT-or-STAGE`.

    An IN_PROGRESS run carries a stage (e.g. PAUSED/HALTED) while it waits on
    a manual trigger step - surface it so paused reads differently to running.
    """
    state = state_obj.get("name", "—")
    result = state_obj.get("result", {}).get("name", "")
    stage = state_obj.get("stage", {}).get("name", "")
    return f"{state}/{result or stage}" if (result or stage) else state


def _format_duration(seconds):
    return f"{seconds // 60}m {seconds % 60}s" if seconds else "—"


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


def _pipeline_steps(config, pipeline_uuid):
    return _paginated_values(
        config, f"/pipelines/{pipeline_uuid}/steps/?pagelen=100"
    )


def _print_pipeline_steps(steps):
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


def _has_failed_step(steps):
    return any(
        s.get("state", {}).get("result", {}).get("name") == "FAILED" for s in steps
    )


def cmd_pipeline(config, pipeline_id):
    """Show one run - build number (`#1333`) or uuid - with its steps."""
    pipeline = _fetch_pipeline(config, pipeline_id)
    uuid = pipeline.get("uuid", "")
    build_number = pipeline.get("build_number", "—")
    target = pipeline.get("target", {})
    selector = target.get("selector", {})

    # A pull-request run names its branch under `source`, not `ref_name`.
    ref = target.get("ref_name") or target.get("source") or "—"
    commit = target.get("commit", {}).get("hash", "")[:12] or "—"
    trigger = (
        pipeline.get("trigger", {}).get("name")
        or target.get("type", "—").replace("pipeline_ref_target", "push")
    )
    creator = pipeline.get("creator", {}).get("display_name", "—")
    created = pipeline.get("created_on", "")[:16].replace("T", " ") or "—"
    completed = pipeline.get("completed_on", "")[:16].replace("T", " ")
    duration = _format_duration(pipeline.get("duration_in_seconds"))

    print(f"## Pipeline #{build_number} — "
          f"{_pipeline_status(pipeline.get('state', {}))}\n")
    print(f"Branch: {ref} | Commit: {commit}")
    if selector.get("type") == "custom":
        print(f"Custom pipeline: {selector.get('pattern', '—')}")
    if target.get("pull_request_id"):
        print(f"Pull request: #{target['pull_request_id']}")
    print(f"Trigger: {trigger} | By: {creator}")
    print(f"Created: {created}"
          + (f" | Finished: {completed}" if completed else "")
          + f" | Duration: {duration}")
    print(f"URL: https://bitbucket.org/{config['workspace']}/{config['repo_slug']}"
          f"/pipelines/results/{build_number}")
    # The full uuid, not its 8-char prefix: it is what the API path takes, so
    # follow-up commands resolve it with no lookup at all.
    print(f"Id: {uuid.strip('{}')}")

    steps = _pipeline_steps(config, uuid)
    if not steps:
        print("\nNo steps found.")
        return

    print()
    _print_pipeline_steps(steps)
    if _has_failed_step(steps):
        print(f"\nFull output: pipeline-log {uuid.strip('{}')}")


def cmd_pipeline_steps(config, pipeline_id):
    """List steps for a pipeline run, including the structured failure
    reason (e.g. memory-limit-exceeded) that log-scraping would miss."""
    pipeline_uuid = _resolve_pipeline_uuid(config, pipeline_id)
    steps = _pipeline_steps(config, pipeline_uuid)
    if not steps:
        print("No steps found.")
        return

    print(f"## Steps for pipeline {pipeline_uuid}\n")
    _print_pipeline_steps(steps)


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

    steps = _pipeline_steps(config, pipeline_uuid)
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
    pipeline_id, rest = _leading_positional("pipeline-log", args, "PIPELINE_ID")
    step_id = None
    lines = 200
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--lines":
            value = _option_value(rest, index, "a positive integer")
            try:
                lines = int(value)
            except ValueError:
                _fail("--lines requires a positive integer")
            if lines <= 0:
                _fail("--lines must be a positive integer")
            index += 2
        elif arg == "--full":
            lines = None
            index += 1
        elif _looks_like_option(arg):
            _fail(f"unknown pipeline-log option: {arg}")
        elif step_id is None:
            step_id = arg
            index += 1
        else:
            _fail("pipeline-log accepts at most one STEP_ID")
    return pipeline_id, step_id, lines


def _parse_pipelines_args(args):
    if not args:
        return 10
    value = _single_positional("pipelines", args, "COUNT")
    try:
        count = int(value)
    except ValueError:
        count = 0
    if not 1 <= count <= MAX_PIPELINE_COUNT:
        _fail(
            f"pipelines COUNT must be an integer between 1 and "
            f"{MAX_PIPELINE_COUNT} (the API page size cap), got {value!r}"
        )
    return count


def cmd_pipelines(config, count=10):
    """List recent pipeline runs."""
    data = api_request(config, f"/pipelines/?pagelen={count}&sort=-created_on")
    pipelines = data.get("values", [])
    if not pipelines:
        print("No pipelines found.")
        return

    print("## Recent Pipelines\n")
    print("| Build | Id | Branch | Status | Duration | Trigger | Created |")
    print("|-------|----|--------|--------|----------|---------|---------|")
    for p in pipelines:
        build = p.get("build_number", "—")
        uuid = p.get("uuid", "—").strip("{}")[:8]
        target = p.get("target", {})
        branch = target.get("ref_name", target.get("selector", {}).get("pattern", "—"))
        status = _pipeline_status(p.get("state", {}))
        dur_str = _format_duration(p.get("duration_in_seconds"))
        trigger = target.get("type", "—").replace("pipeline_ref_target", "push")
        created = p.get("created_on", "")[:16].replace("T", " ")
        print(f"| {build} | {uuid} | {branch} | {status} | {dur_str} | {trigger} "
              f"| {created} |")


def _parse_pipeline_variable(value):
    key, separator, variable_value = value.partition("=")
    if not separator or not key.strip():
        _fail(f"--variable requires KEY=VALUE, got {value!r}")
    return {"key": key.strip(), "value": variable_value}


def _parse_run_pipeline_args(args):
    branch = None
    pattern = None
    variables = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--branch":
            branch = _option_value(args, index, "a branch")
            index += 2
        elif arg == "--custom":
            pattern = _option_value(args, index, "a custom pipeline name")
            index += 2
        elif arg == "--variable":
            variables.append(
                _parse_pipeline_variable(_option_value(args, index, "KEY=VALUE"))
            )
            index += 2
        elif _looks_like_option(arg):
            _fail(f"unknown run-pipeline option: {arg}")
        else:
            _fail(
                f"unexpected run-pipeline argument: {arg!r} "
                "(name a custom pipeline with --custom PATTERN)"
            )
    return branch, pattern, variables


def cmd_run_pipeline(config, branch=None, pattern=None, variables=None):
    """Trigger a pipeline run on a branch."""
    if not branch:
        branch = _git("branch", "--show-current")
        if not branch:
            _fail("could not determine the current branch")

    target = {
        "type": "pipeline_ref_target",
        "ref_type": "branch",
        "ref_name": branch,
    }
    # Without a selector the branch's default definition runs; a custom
    # pipeline is only reachable through an explicit custom selector.
    if pattern:
        target["selector"] = {"type": "custom", "pattern": pattern}
    payload = {"target": target}
    if variables:
        payload["variables"] = variables

    result = api_request(config, "/pipelines/", method="POST", data=payload)
    uuid = result.get("uuid", "").strip("{}")
    build_number = result.get("build_number", "—")
    state = result.get("state", {}).get("name", "—")
    kind = f"custom pipeline '{pattern}'" if pattern else "default pipeline"

    print(f"Started {kind} on {branch}")
    print(f"Pipeline #{build_number} ({state}) — id {uuid}")
    print(
        f"URL: https://bitbucket.org/{config['workspace']}/{config['repo_slug']}"
        f"/pipelines/results/{build_number}"
    )
    if uuid:
        print(f"\nFollow with: pipeline-steps {uuid}")
    return result


USAGE = """Usage: bitbucket_api.py <command> [args]

Commands:
  create-pr <TITLE> [--description TEXT | --description-file PATH]
                    [--source BRANCH] [--destination BRANCH] [--no-close]
                                     Create pull request
  list-prs [STATE] [--source BRANCH | --current-branch]
                                     List PRs (OPEN/MERGED/DECLINED/SUPERSEDED)
  get-pr <ID>                        View PR details
  pr-commits <ID>                    List a PR's non-merge commits and full messages
  update-pr <ID> [--title TEXT] [--description TEXT | --description-file PATH]
                                     Update PR title and/or description
  merge-pr <ID> [--strategy S]       Merge PR (merge_commit/squash/fast_forward)
  approve-pr <ID>                    Approve PR as the configured account
  decline-pr <ID>                    Decline PR
  pr-comments <ID>                   List PR comments
  add-comment <ID> <TEXT>            Add comment to PR
  pipelines [COUNT]                  List recent pipelines
  pipeline <PIPELINE_ID>             Show one run (build number or uuid) with its steps
  run-pipeline [--branch BRANCH] [--custom PATTERN] [--variable KEY=VALUE]
                                     Trigger a pipeline (default: the branch's own definition)
  pipeline-steps <PIPELINE_ID>       List steps + failure reason for a pipeline
  pipeline-log <PIPELINE_ID> [STEP_ID] [--lines N] [--full]
                                     Fetch a step's log (defaults to the first FAILED step, last N lines)

PIPELINE_ID is a build number (1333, or '#1333' quoted so the shell keeps it)
or a full/short pipeline uuid."""


COMMANDS = (
    "create-pr", "list-prs", "get-pr", "pr-commits", "update-pr", "merge-pr",
    "approve-pr", "decline-pr", "pr-comments", "add-comment", "pipelines",
    "pipeline", "run-pipeline", "pipeline-steps", "pipeline-log",
)


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    cmd, args = sys.argv[1], sys.argv[2:]

    if cmd in ("help", "--help", "-h"):
        print(USAGE)
        return

    if cmd not in COMMANDS:
        print(f"Error: unknown command: {cmd}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
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

    if cmd == "create-pr":
        title, description, source, destination, close_source = (
            _parse_create_pr_args(args)
        )
        cmd_create_pr(config, title, description, source, destination, close_source)

    elif cmd == "list-prs":
        state, source_branch = _parse_list_prs_args(args)
        cmd_list_prs(config, state, source_branch=source_branch)

    elif cmd == "get-pr":
        cmd_get_pr(config, _single_pr_id(cmd, args))

    elif cmd == "pr-commits":
        cmd_pr_commits(config, _single_pr_id(cmd, args))

    elif cmd == "update-pr":
        pr_id, title, description = _parse_update_pr_args(args)
        cmd_update_pr(config, pr_id, title=title, description=description)

    elif cmd == "merge-pr":
        pr_id, strategy = _parse_merge_pr_args(args)
        cmd_merge_pr(config, pr_id, strategy)

    elif cmd == "approve-pr":
        cmd_approve_pr(config, _single_pr_id(cmd, args))

    elif cmd == "decline-pr":
        cmd_decline_pr(config, _single_pr_id(cmd, args))

    elif cmd == "pr-comments":
        cmd_pr_comments(config, _single_pr_id(cmd, args))

    elif cmd == "add-comment":
        pr_id, text = _parse_add_comment_args(args)
        cmd_add_comment(config, pr_id, text)

    elif cmd == "pipelines":
        cmd_pipelines(config, _parse_pipelines_args(args))

    elif cmd == "pipeline":
        cmd_pipeline(config, _single_positional(cmd, args, "PIPELINE_ID"))

    elif cmd == "run-pipeline":
        branch, pattern, variables = _parse_run_pipeline_args(args)
        cmd_run_pipeline(config, branch, pattern, variables)

    elif cmd == "pipeline-steps":
        cmd_pipeline_steps(config, _single_positional(cmd, args, "PIPELINE_ID"))

    elif cmd == "pipeline-log":
        pipeline_id, step_id, lines = _parse_pipeline_log_args(args)
        cmd_pipeline_log(config, pipeline_id, step_id, lines)


if __name__ == "__main__":
    main()

import email.message
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import call, patch

from bitbucket_api import (
    COMMANDS,
    api_request,
    _NoRedirect,
    _config_paths,
    _fetch_pipeline,
    _fetch_pipeline_log,
    _paginated_values,
    _parse_add_comment_args,
    _parse_comments_args,
    _parse_create_pr_args,
    _parse_delete_comment_args,
    _parse_edit_comment_args,
    _parse_get_pr_args,
    _parse_list_prs_args,
    _parse_merge_pr_args,
    _parse_pipeline_log_args,
    _parse_remote,
    _parse_pipelines_args,
    _parse_run_pipeline_args,
    _parse_update_pr_args,
    _read_log_lines,
    _resolve_pipeline_uuid,
    _retry_after_seconds,
    _single_positional,
    _single_pr_id,
    load_config,
    cmd_add_comment,
    cmd_approve_pr,
    cmd_comments,
    cmd_delete_comment,
    cmd_edit_comment,
    cmd_get_pr,
    cmd_list_prs,
    cmd_pipeline,
    cmd_pr_commits,
    cmd_run_pipeline,
    main,
)


class ConfigPathsTest(unittest.TestCase):
    def test_derives_skill_local_and_agent_root_configs_for_all_install_roots(self):
        for root in (".claude", ".agents", ".codex"):
            for base in (f"/home/user/{root}", f"/repo/{root}"):
                script = f"{base}/skills/bitbucket/bitbucket_api.py"
                with self.subTest(script=script):
                    self.assertEqual(
                        [
                            f"{base}/skills/bitbucket/bitbucket.config",
                            f"{base}/bitbucket.config",
                        ],
                        _config_paths(script),
                    )

    def test_nonstandard_checkout_only_uses_config_next_to_script(self):
        self.assertEqual(
            ["/repo/bitbucket-skill/bitbucket.config"],
            _config_paths("/repo/bitbucket-skill/bitbucket_api.py"),
        )


class NoRedirectTest(unittest.TestCase):
    def test_returns_redirect_without_forwarding_authorization(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path == "/log":
                    self.send_response(307)
                    self.send_header(
                        "Location",
                        f"http://127.0.0.1:{self.server.server_port}/download",
                    )
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/log",
            headers={"Authorization": "Bearer secret"},
        )

        with opener.open(request) as response:
            self.assertEqual(307, response.status)
            self.assertEqual(
                f"http://127.0.0.1:{server.server_port}/download",
                response.headers["Location"],
            )

        self.assertEqual([("/log", "Bearer secret")], requests)


class PaginationTest(unittest.TestCase):
    @patch("bitbucket_api.api_request")
    def test_fetches_every_page_using_repository_relative_paths(self, api_request):
        config = {"workspace": "acme", "repo_slug": "widgets"}
        api_request.side_effect = [
            {
                "values": [{"uuid": "{step-1}"}],
                "next": (
                    "https://api.bitbucket.org/2.0/repositories/acme/widgets/"
                    "pipelines/{pipeline}/steps/?pagelen=100&page=2"
                ),
            },
            {"values": [{"uuid": "{step-2}"}]},
        ]

        values = _paginated_values(
            config, "/pipelines/{pipeline}/steps/?pagelen=100"
        )

        self.assertEqual([{"uuid": "{step-1}"}, {"uuid": "{step-2}"}], values)
        self.assertEqual(
            [
                ((config, "/pipelines/{pipeline}/steps/?pagelen=100"),),
                ((config, "/pipelines/{pipeline}/steps/?pagelen=100&page=2"),),
            ],
            [call for call in api_request.call_args_list],
        )


class PullRequestCommandsTest(unittest.TestCase):
    @patch("bitbucket_api._paginated_values")
    def test_list_prs_filters_exact_full_source_branch(self, paginated_values):
        target_branch = "feature/IB-123-a-branch-name-longer-than-forty-characters"
        paginated_values.return_value = [
            {
                "id": 10,
                "title": "Target PR",
                "author": {"display_name": "A"},
                "source": {"branch": {"name": target_branch}},
                "updated_on": "2026-07-15T12:00:00Z",
            },
            {
                "id": 11,
                "title": "Different PR",
                "author": {"display_name": "B"},
                "source": {"branch": {"name": target_branch + "-other"}},
                "updated_on": "2026-07-15T12:00:00Z",
            },
        ]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_list_prs({}, "OPEN", source_branch=target_branch)

        paginated_values.assert_called_once_with(
            {}, "/pullrequests?state=OPEN&pagelen=25"
        )
        self.assertIn(target_branch, stdout.getvalue())
        self.assertIn("Target PR", stdout.getvalue())
        self.assertNotIn("Different PR", stdout.getvalue())

    @patch("bitbucket_api._paginated_values")
    def test_pr_commits_uses_pr_endpoint_and_excludes_merges(self, paginated_values):
        paginated_values.return_value = [
            {
                "hash": "regular",
                "message": "fix: preserve the full message\n\nBody line",
                "parents": [{"hash": "parent"}],
            },
            {
                "hash": "merge",
                "message": "Merge branch 'main'",
                "parents": [{"hash": "one"}, {"hash": "two"}],
            },
        ]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_pr_commits({}, 42)

        paginated_values.assert_called_once_with(
            {}, "/pullrequests/42/commits?pagelen=50"
        )
        self.assertIn("regular", stdout.getvalue())
        self.assertIn("Body line", stdout.getvalue())
        self.assertNotIn("Merge branch", stdout.getvalue())

    @patch("bitbucket_api.api_request")
    def test_approve_pr_posts_to_approve_endpoint(self, api_request):
        api_request.return_value = {
            "approved": True,
            "state": "approved",
            "user": {"display_name": "Reviewer Name"},
        }
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_approve_pr({}, 42)

        api_request.assert_called_once_with(
            {}, "/pullrequests/42/approve", method="POST"
        )
        self.assertIn("Approved PR #42", stdout.getvalue())
        self.assertIn("Reviewer Name", stdout.getvalue())
        self.assertIn("approved", stdout.getvalue())


class ApiRequestHeadersTest(unittest.TestCase):
    def _sent_request(self, data):
        config = {"workspace": "ws", "repo_slug": "repo"}
        response = io.BytesIO(b"{}")
        with patch("bitbucket_api._auth_header", return_value="Basic x"), \
                patch("bitbucket_api.urllib.request.urlopen",
                      return_value=response) as urlopen:
            api_request(config, "/pullrequests/1/approve", method="POST", data=data)
        return urlopen.call_args.args[0]

    def test_bodyless_post_sends_no_content_type(self):
        self.assertIsNone(self._sent_request(None).get_header("Content-type"))

    def test_post_with_body_sends_json_content_type(self):
        request = self._sent_request({"a": 1})
        self.assertEqual("application/json", request.get_header("Content-type"))

    def test_an_empty_delete_response_reads_as_an_empty_object(self):
        config = {"workspace": "ws", "repo_slug": "repo"}
        with patch("bitbucket_api._auth_header", return_value="Basic x"), \
                patch("bitbucket_api.urllib.request.urlopen",
                      return_value=io.BytesIO(b"")):
            self.assertEqual(
                {}, api_request(config, "/pullrequests/1/comments/7", method="DELETE")
            )

    def test_an_empty_get_response_is_an_error_not_an_empty_object(self):
        config = {"workspace": "ws", "repo_slug": "repo"}
        with patch("bitbucket_api._auth_header", return_value="Basic x"), \
                patch("bitbucket_api.urllib.request.urlopen",
                      return_value=io.BytesIO(b"")), \
                self.assertRaises(ValueError):
            api_request(config, "/pullrequests/1/comments/7")


class ApiRequestRateLimitTest(unittest.TestCase):
    def setUp(self):
        patch("bitbucket_api._auth_header", return_value="Basic x").start()
        self.urlopen = patch("bitbucket_api.urllib.request.urlopen").start()
        self.sleep = patch("bitbucket_api.time.sleep").start()
        self.addCleanup(patch.stopall)
        self.stderr = io.StringIO()

    @staticmethod
    def _rate_limited(retry_after=None):
        headers = email.message.Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError(
            "https://api.bitbucket.org/2.0/repositories/ws/repo/pullrequests/42/comments",
            429, "Too Many Requests", headers,
            io.BytesIO(b"Rate limit for this resource has been exceeded"),
        )

    def _post_comment(self):
        with redirect_stderr(self.stderr):
            return api_request(
                {"workspace": "ws", "repo_slug": "repo"}, "/pullrequests/42/comments",
                method="POST", data={"content": {"raw": "Why"}},
            )

    def _waits(self):
        return [sleep.args[0] for sleep in self.sleep.call_args_list]

    def test_waits_as_long_as_retry_after_says_then_resends_the_request(self):
        self.urlopen.side_effect = [
            self._rate_limited(retry_after="7"), io.BytesIO(b'{"id": 1}'),
        ]

        self.assertEqual({"id": 1}, self._post_comment())

        self.assertEqual([7], self._waits())
        resent = self.urlopen.call_args.args[0]
        self.assertEqual(
            ("POST", b'{"content": {"raw": "Why"}}'), (resent.get_method(), resent.data)
        )
        self.assertIn(
            "Rate limited (429) on POST /pullrequests/42/comments; retry 1 of 4 "
            "in 7 s [rate-limit headers: Retry-After: 7]",
            self.stderr.getvalue(),
        )

    def test_backs_off_exponentially_without_retry_after(self):
        self.urlopen.side_effect = [self._rate_limited() for _ in range(3)] + [
            io.BytesIO(b"{}")
        ]

        self.assertEqual({}, self._post_comment())

        self.assertEqual([30, 60, 120], self._waits())
        self.assertIn("[rate-limit headers: none]", self.stderr.getvalue())

    def test_fails_with_the_server_message_once_the_retries_are_spent(self):
        self.urlopen.side_effect = [self._rate_limited() for _ in range(5)]

        with self.assertRaises(SystemExit) as raised:
            self._post_comment()

        self.assertEqual(1, raised.exception.code)
        self.assertEqual(5, self.urlopen.call_count)
        self.assertEqual([30, 60, 120, 240], self._waits())
        self.assertIn(
            "Error 429: Rate limit for this resource has been exceeded\n"
            "Gave up on POST /pullrequests/42/comments after 5 attempts",
            self.stderr.getvalue(),
        )

    def test_fails_at_once_when_retry_after_asks_for_more_than_it_waits(self):
        self.urlopen.side_effect = [self._rate_limited(retry_after="3600")]

        with self.assertRaises(SystemExit):
            self._post_comment()

        self.sleep.assert_not_called()
        self.assertIn(
            "Bitbucket asks to retry POST /pullrequests/42/comments in 3600 s",
            self.stderr.getvalue(),
        )

    def test_reads_retry_after_as_seconds_or_an_http_date(self):
        now = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc).timestamp()
        cases = [
            ("45", 45),
            (" 0 ", 0),
            ("Mon, 26 Oct 2026 11:01:30 GMT", 90),
            ("Mon, 26 Oct 2026 10:59:00 GMT", 0),
            ("soon", None),
            ("", None),
            (None, None),
        ]
        for value, seconds in cases:
            with self.subTest(value=value):
                self.assertEqual(seconds, _retry_after_seconds(value, now))


class ListPullRequestArgumentsTest(unittest.TestCase):
    def test_parses_state_and_source_filters(self):
        self.assertEqual(("OPEN", None), _parse_list_prs_args([]))
        self.assertEqual(
            ("MERGED", "feature/IB-123"),
            _parse_list_prs_args(["merged", "--source", "feature/IB-123"]),
        )
        with patch("bitbucket_api._git", return_value="feature/current"):
            self.assertEqual(
                ("OPEN", "feature/current"),
                _parse_list_prs_args(["--current-branch"]),
            )

    def test_rejects_invalid_arguments(self):
        cases = [
            ["--source"],
            ["--source", "--current-branch"],
            ["--unknown"],
            ["OPEN", "MERGED"],
            ["INVALID"],
            ["--source", "one", "--current-branch"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_list_prs_args(args)


def _description_file(test, text):
    handle, path = tempfile.mkstemp()
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(text)
    test.addCleanup(os.unlink, path)
    return path


class CreatePullRequestArgumentsTest(unittest.TestCase):
    def test_defaults_when_only_a_title_is_given(self):
        self.assertEqual(
            ("feat: x", "", None, "master", True), _parse_create_pr_args(["feat: x"])
        )

    def test_parses_description_source_destination_and_no_close(self):
        self.assertEqual(
            ("feat: x", "Summary", "feature/IB-123", "develop", False),
            _parse_create_pr_args([
                "feat: x",
                "--description", "Summary",
                "--source", "feature/IB-123",
                "--destination", "develop",
                "--no-close",
            ]),
        )

    def test_reads_description_from_file(self):
        path = _description_file(self, "## Summary\n\nLong body\n")

        self.assertEqual(
            ("feat: x", "## Summary\n\nLong body\n", None, "master", True),
            _parse_create_pr_args(["feat: x", "--description-file", path]),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            [""],
            ["--description", "only a description"],
            ["t", "--description"],
            ["t", "--description-file"],
            ["t", "--description-file", "/nonexistent/description.md"],
            ["t", "--description", "a", "--description-file", "/tmp/b.md"],
            ["t", "--description", "a", "--description", "b"],
            ["t", "--source"],
            ["t", "--source", "--no-close"],
            ["t", "--destination"],
            ["t", "--description", "--source", "b"],
            ["t", "--unknown"],
            ["t", "--descriptionfile", "/tmp/b.md"],
            ["fix:", "unquoted title tail"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_create_pr_args(args)


class UpdatePullRequestArgumentsTest(unittest.TestCase):
    def test_parses_title_and_description_sources(self):
        self.assertEqual(
            ("42", "New title", "New body"),
            _parse_update_pr_args([
                "42", "--title", "New title", "--description", "New body",
            ]),
        )
        path = _description_file(self, "From file")
        self.assertEqual(
            ("42", None, "From file"),
            _parse_update_pr_args(["42", "--description-file", path]),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["42"],
            ["--title", "no id"],
            ["not-an-id", "--title", "T"],
            ["42", "--title"],
            ["42", "--title", " "],
            ["42", "--title", "--description", "body"],
            ["42", "--description"],
            ["42", "--description-file"],
            ["42", "--description-file", "/nonexistent/description.md"],
            ["42", "--description", "a", "--description-file", "/tmp/b.md"],
            ["42", "--unknown"],
            ["42", "stray"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_update_pr_args(args)


class MergePullRequestArgumentsTest(unittest.TestCase):
    def test_defaults_to_merge_commit_and_accepts_known_strategies(self):
        self.assertEqual(
            ("42", "merge_commit", None, True), _parse_merge_pr_args(["42"])
        )
        self.assertEqual(
            ("42", "squash", None, True),
            _parse_merge_pr_args(["42", "--strategy", "squash"]),
        )

    def test_reads_the_merge_message_inline_or_from_a_file(self):
        self.assertEqual(
            ("42", "merge_commit", "ship it [skip-ci]", True),
            _parse_merge_pr_args(["42", "--message", "ship it [skip-ci]"]),
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("from a file\n")
            path = handle.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(
            ("42", "merge_commit", "from a file\n", True),
            _parse_merge_pr_args(["42", "--message-file", path]),
        )

    def test_no_close_keeps_the_source_branch(self):
        self.assertEqual(
            ("42", "merge_commit", None, False),
            _parse_merge_pr_args(["42", "--no-close"]),
        )
        self.assertEqual(
            ("42", "squash", "done", False),
            _parse_merge_pr_args(
                ["42", "--no-close", "--strategy", "squash",
                 "--message", "done"]
            ),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["--strategy", "squash"],
            ["abc"],
            ["42", "--strategy"],
            ["42", "--strategy", "rebase"],
            ["42", "--unknown"],
            ["42", "43"],
            ["42", "--message"],
            ["42", "--message", "a", "--message-file", "/tmp/b.txt"],
            ["42", "--message-file", "/nonexistent/merge-message.txt"],
            ["42", "--no-close", "extra"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_merge_pr_args(args)


class AddCommentArgumentsTest(unittest.TestCase):
    def test_takes_the_body_from_a_flag_a_file_or_one_quoted_argument(self):
        self.assertEqual(
            ("42", "LGTM", None, None, False, None, None),
            _parse_add_comment_args(["42", "--body", "LGTM"]),
        )
        path = _description_file(self, "## Finding\n\nDetails\n")
        self.assertEqual(
            ("42", "## Finding\n\nDetails\n", None, None, False, None, None),
            _parse_add_comment_args(["42", "--body-file", path]),
        )
        self.assertEqual(
            ("42", "ship it now", None, None, False, None, None),
            _parse_add_comment_args(["42", "ship it now"]),
        )
        self.assertEqual(
            ("42", "--- a rule", None, None, False, None, None),
            _parse_add_comment_args(["42", "--- a rule"]),
        )

    def test_parses_an_inline_pending_draft(self):
        self.assertEqual(
            ("42", "Use DI", "src/Model.php", 45, True, None, None),
            _parse_add_comment_args([
                "42", "--path", "src/Model.php", "--line", "45",
                "--pending", "--body", "Use DI",
            ]),
        )

    def test_parses_an_old_side_and_a_file_anchor(self):
        self.assertEqual(
            ("42", "Why removed", "src/ATests.java", None, True, None, 120),
            _parse_add_comment_args([
                "42", "--path", "src/ATests.java", "--old-line", "120",
                "--pending", "--body", "Why removed",
            ]),
        )
        self.assertEqual(
            ("42", "What changed", "src/ATests.java", None, True, None, None),
            _parse_add_comment_args([
                "42", "--path", "src/ATests.java", "--pending", "--body", "What changed",
            ]),
        )

    def test_parses_a_reply(self):
        self.assertEqual(
            ("42", "Done", None, None, False, 886718944, None),
            _parse_add_comment_args(["42", "--reply-to", "886718944", "--body", "Done"]),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["42"],
            ["42", "   "],
            ["42", "--body", " "],
            ["abc", "--body", "text"],
            ["--body", "hi"],
            ["42", "--file", "notes.md"],
            ["42", "--body"],
            ["42", "--body", "--pending"],
            ["42", "--body", "a", "--body-file", "/tmp/b.md"],
            ["42", "--body-file", "/nonexistent/comment.md"],
            ["42", "text", "--body", "other"],
            # Unquoted words are no longer joined: one of them could be an
            # option meant as prose, or prose meant as an option.
            ["42", "ship", "it", "now"],
            ["42", "---", "a", "rule"],
            ["42", "--body", "x", "--line", "3"],
            ["42", "--body", "x", "--old-line", "3"],
            ["42", "--body", "x", "--path", "src/a.py", "--old-line", "0"],
            ["42", "--body", "x", "--path", "src/a.py", "--old-line"],
            ["42", "--body", "x", "--path", "src/a.py", "--line", "3", "--old-line", "3"],
            ["42", "--body", "x", "--path", "src/a.py", "--line", "0"],
            ["42", "--body", "x", "--path", "src/a.py", "--line", "three"],
            ["42", "--body", "x", "--path"],
            ["42", "--body", "x", "--reply-to"],
            ["42", "--body", "x", "--reply-to", "abc"],
            ["42", "--body", "x", "--reply-to", "0"],
            ["42", "--body", "x", "--reply-to", "7", "--path", "src/a.py", "--line", "3"],
            ["42", "--body", "x", "--reply-to", "7", "--path", "src/a.py"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_add_comment_args(args)


class EditCommentArgumentsTest(unittest.TestCase):
    def test_takes_the_new_body_from_a_flag_or_a_file(self):
        self.assertEqual(
            ("42", "886718944", "Reworded"),
            _parse_edit_comment_args(["42", "886718944", "--body", "Reworded"]),
        )
        path = _description_file(self, "From file\n")
        self.assertEqual(
            ("42", "7", "From file\n"),
            _parse_edit_comment_args(["42", "7", "--body-file", path]),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["42"],
            ["42", "--body", "x"],
            ["42", "7"],
            ["42", "abc", "--body", "x"],
            ["42", "7", "--body"],
            ["42", "7", "--body", " "],
            ["42", "7", "--body", "a", "--body-file", "/tmp/b.md"],
            ["42", "7", "Reworded"],
            # A draft is never published by an edit: there is no flag for it.
            ["42", "7", "--body", "x", "--pending"],
            ["42", "7", "--body", "x", "--path", "src/a.py"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_edit_comment_args(args)


class DeleteCommentArgumentsTest(unittest.TestCase):
    def test_takes_the_comment_and_an_explicit_published_flag(self):
        self.assertEqual(("42", "7", False), _parse_delete_comment_args(["42", "7"]))
        self.assertEqual(
            ("42", "7", True), _parse_delete_comment_args(["42", "7", "--published"])
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["42"],
            ["42", "abc"],
            ["42", "0"],
            ["42", "7", "8"],
            ["42", "7", "--pending"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_delete_comment_args(args)


class CommentsArgumentsTest(unittest.TestCase):
    def test_parses_pending_only_and_json(self):
        self.assertEqual(("42", False, False), _parse_comments_args("comments", ["42"]))
        self.assertEqual(
            ("42", True, True),
            _parse_comments_args("comments", ["42", "--json", "--pending-only"]),
        )

    def test_rejects_invalid_arguments(self):
        for args in ([], ["--json"], ["abc"], ["42", "43"], ["42", "--all"]):
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_comments_args("comments", args)


class CommentCommandsTest(unittest.TestCase):
    LONG_TEXT = "Finding. " + "x" * 2000 + " END"
    DRAFT = {
        "id": 886718944,
        "pending": True,
        "user": {"display_name": "Reviewer Name"},
        "inline": {"path": "src/Validator.java", "from": None, "to": 932},
        "created_on": "2026-10-21T10:12:33.000000+00:00",
        "content": {"raw": LONG_TEXT},
    }
    PUBLISHED = {
        "id": 886718700,
        "pending": False,
        "user": {"display_name": "Author Name"},
        "parent": {"id": 886718600},
        "created_on": "2026-10-20T08:00:00.000000+00:00",
        "content": {"raw": "General remark"},
    }

    @patch("bitbucket_api._paginated_values")
    def test_comments_prints_full_text_anchor_and_pending_state(
            self, paginated_values):
        paginated_values.return_value = [self.DRAFT, self.PUBLISHED]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_comments({}, "42")

        paginated_values.assert_called_once_with(
            {}, "/pullrequests/42/comments?pagelen=100"
        )
        output = stdout.getvalue()
        self.assertIn("## Comments on PR #42 (2)", output)
        self.assertIn("### Comment 886718944", output)
        self.assertIn(
            "Pending: true | Author: Reviewer Name | "
            "Where: src/Validator.java:932 | Created: 2026-10-21 10:12",
            output,
        )
        self.assertIn(self.LONG_TEXT, output)
        self.assertIn("Pending: false", output)
        self.assertIn("Where: general", output)
        self.assertIn("Reply to: 886718600", output)

    @patch("bitbucket_api._paginated_values")
    def test_pending_only_keeps_drafts_and_json_dumps_the_raw_objects(
            self, paginated_values):
        paginated_values.return_value = [self.DRAFT, self.PUBLISHED]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_comments({}, "42", pending_only=True, as_json=True)

        self.assertEqual([self.DRAFT], json.loads(stdout.getvalue()))

    @patch("bitbucket_api._paginated_values", return_value=[])
    def test_reports_when_there_are_no_drafts(self, paginated_values):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_comments({}, "42", pending_only=True)

        self.assertEqual("No pending comments on PR #42.\n", stdout.getvalue())

    @patch("bitbucket_api.api_request")
    def test_add_comment_posts_an_inline_pending_draft(self, api_request):
        api_request.return_value = dict(self.DRAFT, content={"raw": "Use DI"})
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_add_comment({}, "42", "Use DI", "src/Validator.java", 932, True)

        api_request.assert_called_once_with(
            {},
            "/pullrequests/42/comments",
            method="POST",
            data={
                "content": {"raw": "Use DI"},
                "inline": {"path": "src/Validator.java", "to": 932},
                "pending": True,
            },
        )
        self.assertIn("Added comment 886718944 to PR #42", stdout.getvalue())
        self.assertIn(
            "Pending: true | Where: src/Validator.java:932", stdout.getvalue()
        )

    @patch("bitbucket_api.api_request")
    def test_add_comment_anchors_on_the_old_side_or_the_whole_file(self, api_request):
        cases = [
            ({"old_line": 120}, {"path": "src/ATests.java", "from": 120},
             "Where: src/ATests.java:120 (old side)"),
            ({}, {"path": "src/ATests.java"}, "Where: src/ATests.java (file)"),
        ]
        for kwargs, inline, where in cases:
            with self.subTest(inline=inline):
                api_request.reset_mock()
                api_request.return_value = dict(self.DRAFT, inline=inline)
                stdout = io.StringIO()

                with redirect_stdout(stdout):
                    cmd_add_comment(
                        {}, "42", "Why", "src/ATests.java", pending=True, **kwargs
                    )

                self.assertEqual(
                    {"content": {"raw": "Why"}, "inline": inline, "pending": True},
                    api_request.call_args.kwargs["data"],
                )
                self.assertIn(where, stdout.getvalue())

    @patch("bitbucket_api.api_request")
    def test_add_comment_replies_in_the_parent_thread(self, api_request):
        api_request.return_value = dict(self.PUBLISHED, parent={"id": 886718944})
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_add_comment({}, "42", "Done", reply_to=886718944)

        self.assertEqual(
            {"content": {"raw": "Done"}, "parent": {"id": 886718944}},
            api_request.call_args.kwargs["data"],
        )
        self.assertIn("Reply to: 886718944", stdout.getvalue())

    @patch("bitbucket_api.api_request")
    def test_add_comment_without_pending_sends_no_pending_field(self, api_request):
        api_request.return_value = self.PUBLISHED

        with redirect_stdout(io.StringIO()):
            cmd_add_comment({}, "42", "General remark")

        self.assertEqual(
            {"content": {"raw": "General remark"}},
            api_request.call_args.kwargs["data"],
        )

    @patch("bitbucket_api.api_request")
    def test_add_comment_fails_when_a_draft_comes_back_published(self, api_request):
        api_request.return_value = dict(self.DRAFT, pending=False)
        stderr = io.StringIO()

        with redirect_stdout(io.StringIO()), redirect_stderr(stderr), \
                self.assertRaises(SystemExit):
            cmd_add_comment({}, "42", "Use DI", pending=True)

        self.assertIn("Bitbucket published it", stderr.getvalue())

    @patch("bitbucket_api.api_request")
    def test_delete_comment_removes_a_draft_and_confirms_it_is_gone(self, api_request):
        api_request.side_effect = [self.DRAFT, {}, None]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_delete_comment({}, "42", "886718944")

        path = "/pullrequests/42/comments/886718944"
        self.assertEqual(
            [((({}, path)), {}), (({}, path), {"method": "DELETE"}),
             (({}, path), {"allow_404": True})],
            [(c.args, c.kwargs) for c in api_request.call_args_list],
        )
        self.assertIn("Deleted comment 886718944 from PR #42", stdout.getvalue())
        self.assertIn("Pending: true | Where: src/Validator.java:932", stdout.getvalue())

    @patch("bitbucket_api.api_request")
    def test_delete_comment_refuses_a_published_comment_unless_told(self, api_request):
        api_request.return_value = self.PUBLISHED
        stderr = io.StringIO()

        with redirect_stdout(io.StringIO()), redirect_stderr(stderr), \
                self.assertRaises(SystemExit):
            cmd_delete_comment({}, "42", "886718700")

        self.assertEqual(1, api_request.call_count)
        self.assertIn("--published", stderr.getvalue())

    @patch("bitbucket_api.api_request")
    def test_delete_comment_takes_a_published_comment_with_the_flag(self, api_request):
        api_request.side_effect = [self.PUBLISHED, {}, dict(self.PUBLISHED, deleted=True)]

        with redirect_stdout(io.StringIO()):
            cmd_delete_comment({}, "42", "886718700", published=True)

        self.assertEqual("DELETE", api_request.call_args_list[1].kwargs["method"])

    @patch("bitbucket_api.api_request")
    def test_delete_comment_fails_when_the_comment_survives(self, api_request):
        api_request.side_effect = [self.DRAFT, {}, self.DRAFT]

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            cmd_delete_comment({}, "42", "886718944")

    @patch("bitbucket_api.api_request")
    def test_delete_comment_fails_when_the_re_read_proves_nothing(self, api_request):
        api_request.side_effect = [self.DRAFT, {}, {}]

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            cmd_delete_comment({}, "42", "886718944")

    @patch("bitbucket_api.api_request")
    def test_edit_comment_sends_only_the_text_and_confirms_it_stayed_pending(
            self, api_request):
        api_request.side_effect = [
            self.DRAFT, {}, dict(self.DRAFT, content={"raw": "Reworded"}),
        ]
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_edit_comment({}, "42", "886718944", "Reworded")

        path = "/pullrequests/42/comments/886718944"
        self.assertEqual(
            [
                call({}, path),
                call({}, path, method="PUT", data={"content": {"raw": "Reworded"}}),
                call({}, path),
            ],
            api_request.call_args_list,
        )
        self.assertIn(
            "Pending: true (unchanged) | Where: src/Validator.java:932 (unchanged)",
            stdout.getvalue(),
        )

    @patch("bitbucket_api.api_request")
    def test_edit_comment_fails_loudly_when_the_draft_was_published(
            self, api_request):
        api_request.side_effect = [self.DRAFT, {}, dict(self.DRAFT, pending=False)]
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr), \
                self.assertRaises(SystemExit):
            cmd_edit_comment({}, "42", "886718944", "Reworded")

        self.assertIn("Pending: true → false (CHANGED)", stdout.getvalue())
        self.assertIn("changed its Pending", stderr.getvalue())


class GetPullRequestTest(unittest.TestCase):
    PR = {
        "id": 42,
        "title": "feat: x",
        "state": "OPEN",
        "source": {"branch": {"name": "feature/x"}},
        "destination": {"branch": {"name": "main"}},
        "links": {"html": {"href": "https://bitbucket.org/acme/widgets/pull-requests/42"}},
        "description": "Summary. " + "y" * 1000 + " TAIL",
    }

    def test_parses_full(self):
        self.assertEqual(("42", False), _parse_get_pr_args(["42"]))
        self.assertEqual(("42", True), _parse_get_pr_args(["42", "--full"]))
        for args in ([], ["--full"], ["42", "43"], ["42", "--all"]):
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_get_pr_args(args)

    @patch("bitbucket_api.api_request")
    def test_previews_the_description_unless_full(self, api_request):
        api_request.return_value = self.PR
        preview, full = io.StringIO(), io.StringIO()

        with redirect_stdout(preview):
            cmd_get_pr({}, "42")
        with redirect_stdout(full):
            cmd_get_pr({}, "42", full=True)

        self.assertNotIn("TAIL", preview.getvalue())
        self.assertIn("first 500 of 1014 chars; pass --full", preview.getvalue())
        self.assertIn(self.PR["description"], full.getvalue())
        self.assertNotIn("pass --full", full.getvalue())


class LoadConfigTest(unittest.TestCase):
    def _load(self, config):
        path = _description_file(self, json.dumps(config))
        with patch("bitbucket_api.CONFIG_PATHS", [path]):
            return load_config()

    @patch("bitbucket_api._git", return_value="git@ssh.bitbucket.org:acme/widgets.git")
    def test_fills_workspace_and_repo_from_the_git_remote(self, _git):
        config = self._load({"username": "u", "app_password": "p"})

        self.assertEqual(("acme", "widgets"), (config["workspace"], config["repo_slug"]))

    @patch("bitbucket_api._git", return_value="git@bitbucket.org:acme/widgets.git")
    def test_values_in_the_config_take_precedence(self, _git):
        config = self._load({"app_password": "p", "workspace": "upstream"})

        self.assertEqual(("upstream", "widgets"), (config["workspace"], config["repo_slug"]))

    @patch("bitbucket_api._git", return_value="git@bitbucket.org:acme/widgets.git")
    def test_blank_values_in_the_config_are_filled_from_the_remote(self, _git):
        config = self._load({"app_password": "p", "workspace": "", "repo_slug": None})

        self.assertEqual(("acme", "widgets"), (config["workspace"], config["repo_slug"]))

    @patch("bitbucket_api._git", return_value="")
    def test_exits_with_a_hint_outside_a_bitbucket_checkout(self, _git):
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            self._load({"app_password": "p"})

        self.assertIn("Could not detect workspace/repo", stderr.getvalue())


class RemoteParsingTest(unittest.TestCase):
    def test_parses_bitbucket_ssh_and_https_remotes(self):
        cases = {
            "git@bitbucket.org:acme/widgets.git": ("acme", "widgets"),
            "git@ssh.bitbucket.org:acme/widgets.git": ("acme", "widgets"),
            "ssh://git@altssh.bitbucket.org:443/acme/widgets.git": ("acme", "widgets"),
            "https://user@bitbucket.org/acme/widgets.git": ("acme", "widgets"),
            "https://bitbucket.org/acme/widgets": ("acme", "widgets"),
            "git@bitbucket.org:acme/my.github-tools.git": ("acme", "my.github-tools"),
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(expected, _parse_remote(remote))

    def test_rejects_other_hosts_and_incomplete_paths(self):
        for remote in (
            "",
            "git@github.com:acme/widgets.git",
            "git@not-bitbucket.org:acme/widgets.git",
            "https://evil.example/bitbucket.org/acme/widgets",
            "https://bitbucket.org.evil.example/acme/widgets",
            "git@bitbucket.org:acme",
        ):
            with self.subTest(remote=remote):
                self.assertEqual((None, None), _parse_remote(remote))


class PipelinesArgumentsTest(unittest.TestCase):
    def test_parses_count_with_a_default(self):
        self.assertEqual(10, _parse_pipelines_args([]))
        self.assertEqual(25, _parse_pipelines_args(["25"]))

    def test_rejects_invalid_arguments(self):
        cases = [["many"], ["0"], ["-5"], ["101"], ["--unknown"], ["10", "20"]]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_pipelines_args(args)


class RunPipelineArgumentsTest(unittest.TestCase):
    def test_parses_branch_custom_pattern_and_repeated_variables(self):
        self.assertEqual((None, None, []), _parse_run_pipeline_args([]))
        self.assertEqual(
            ("release/1.2", "deploy-to-production",
             [{"key": "ENV", "value": "prod"}, {"key": "DRY_RUN", "value": ""}]),
            _parse_run_pipeline_args([
                "--branch", "release/1.2",
                "--custom", "deploy-to-production",
                "--variable", "ENV=prod",
                "--variable", "DRY_RUN=",
            ]),
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            ["--branch"],
            ["--custom"],
            ["--custom", "--variable"],
            ["--variable"],
            ["--variable", "ENV"],
            ["--variable", "=prod"],
            ["--unknown"],
            ["deploy-to-production"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_run_pipeline_args(args)


class RunPipelineCommandTest(unittest.TestCase):
    CONFIG = {"workspace": "acme", "repo_slug": "widgets"}

    @patch("bitbucket_api.api_request")
    def test_posts_a_custom_selector_with_variables(self, api_request):
        api_request.return_value = {
            "uuid": "{d4a04f6c-1111-2222-3333-444455556666}",
            "build_number": 77,
            "state": {"name": "PENDING"},
        }
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_run_pipeline(
                self.CONFIG,
                "release/1.2",
                "deploy-to-production",
                [{"key": "ENV", "value": "prod"}],
            )

        api_request.assert_called_once_with(
            self.CONFIG,
            "/pipelines/",
            method="POST",
            data={
                "target": {
                    "type": "pipeline_ref_target",
                    "ref_type": "branch",
                    "ref_name": "release/1.2",
                    "selector": {"type": "custom", "pattern": "deploy-to-production"},
                },
                "variables": [{"key": "ENV", "value": "prod"}],
            },
        )
        output = stdout.getvalue()
        self.assertIn("deploy-to-production", output)
        self.assertIn("Pipeline #77 (PENDING)", output)
        self.assertIn("pipelines/results/77", output)
        # The full uuid, so the follow-up command needs no lookup.
        self.assertIn(
            "pipeline-steps d4a04f6c-1111-2222-3333-444455556666", output
        )

    @patch("bitbucket_api.api_request")
    @patch("bitbucket_api._git", return_value="feature/current")
    def test_defaults_to_the_current_branch_and_omits_the_selector(
            self, _git, api_request):
        api_request.return_value = {"uuid": "{abc}", "build_number": 1}

        with redirect_stdout(io.StringIO()):
            cmd_run_pipeline(self.CONFIG)

        self.assertEqual(
            {
                "target": {
                    "type": "pipeline_ref_target",
                    "ref_type": "branch",
                    "ref_name": "feature/current",
                }
            },
            api_request.call_args.kwargs["data"],
        )

    @patch("bitbucket_api.api_request")
    @patch("bitbucket_api._git", return_value="")
    def test_fails_when_the_branch_cannot_be_determined(self, _git, api_request):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cmd_run_pipeline(self.CONFIG)

        api_request.assert_not_called()


class PipelineIdResolutionTest(unittest.TestCase):
    CONFIG = {"workspace": "acme", "repo_slug": "widgets"}
    RUN = {"uuid": "{d4a04f6c-1111-2222-3333-444455556666}", "build_number": 1333}

    @patch("bitbucket_api.api_request")
    def test_resolves_a_build_number_with_or_without_a_hash(self, api_request):
        for pipeline_id in ("1333", "#1333", " #1333 "):
            with self.subTest(pipeline_id=pipeline_id):
                api_request.reset_mock()
                api_request.return_value = self.RUN

                self.assertEqual(self.RUN, _fetch_pipeline(self.CONFIG, pipeline_id))
                api_request.assert_called_once_with(
                    self.CONFIG, "/pipelines/1333", allow_404=True
                )

    @patch("bitbucket_api.api_request")
    def test_falls_back_to_the_recent_listing_when_the_number_is_not_resolved(
            self, api_request):
        api_request.side_effect = [None, {"values": [self.RUN]}]

        self.assertEqual(self.RUN, _fetch_pipeline(self.CONFIG, "#1333"))
        self.assertEqual(
            "/pipelines/?pagelen=100&sort=-created_on",
            api_request.call_args.args[1],
        )

    @patch("bitbucket_api.api_request")
    def test_an_all_digit_id_can_still_match_a_uuid_prefix(self, api_request):
        run = {"uuid": "{13330000-1111-2222-3333-444455556666}", "build_number": 7}
        api_request.side_effect = [None, {"values": [run]}]

        self.assertEqual(run, _fetch_pipeline(self.CONFIG, "1333"))

    @patch("bitbucket_api.api_request")
    def test_resolves_a_short_uuid_from_the_recent_listing(self, api_request):
        api_request.return_value = {"values": [self.RUN]}

        self.assertEqual(self.RUN, _fetch_pipeline(self.CONFIG, "d4a04f6c"))
        api_request.assert_called_once_with(
            self.CONFIG, "/pipelines/?pagelen=100&sort=-created_on"
        )

    @patch("bitbucket_api.api_request")
    def test_a_full_uuid_needs_no_lookup(self, api_request):
        uuid = "d4a04f6c-1111-2222-3333-444455556666"

        for pipeline_id in (uuid, f"{{{uuid}}}", uuid.upper()):
            with self.subTest(pipeline_id=pipeline_id):
                self.assertEqual(
                    f"{{{uuid}}}", _resolve_pipeline_uuid(self.CONFIG, pipeline_id)
                )
        api_request.assert_not_called()

    @patch("bitbucket_api.api_request")
    def test_a_full_uuid_is_fetched_directly_without_scanning_the_listing(
            self, api_request):
        uuid = "d4a04f6c-1111-2222-3333-444455556666"
        api_request.return_value = self.RUN

        self.assertEqual(self.RUN, _fetch_pipeline(self.CONFIG, uuid))
        api_request.assert_called_once_with(self.CONFIG, f"/pipelines/{{{uuid}}}")

    @patch("bitbucket_api.api_request")
    def test_reports_an_unknown_build_number(self, api_request):
        api_request.side_effect = [None, {"values": [self.RUN]}]
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            _fetch_pipeline(self.CONFIG, "#9999")

        self.assertIn("build #9999", stderr.getvalue())


class PipelineCommandTest(unittest.TestCase):
    CONFIG = {"workspace": "acme", "repo_slug": "widgets"}
    RUN = {
        "uuid": "{d4a04f6c-1111-2222-3333-444455556666}",
        "build_number": 1333,
        "state": {"name": "COMPLETED", "result": {"name": "FAILED"}},
        "target": {
            "type": "pipeline_ref_target",
            "ref_name": "feature/IB-123",
            "commit": {"hash": "abcdef1234567890"},
            "selector": {"type": "custom", "pattern": "deploy"},
        },
        "trigger": {"name": "PUSH"},
        "creator": {"display_name": "Ann Dev"},
        "created_on": "2026-08-14T09:12:33.000Z",
        "completed_on": "2026-08-14T09:16:45.000Z",
        "duration_in_seconds": 252,
    }
    STEPS = [
        {
            "uuid": "{aaaa1111-...}",
            "name": "Build",
            "state": {
                "name": "COMPLETED",
                "result": {
                    "name": "FAILED",
                    "error": {"message": "Container 'build' exceeded memory limit."},
                },
            },
        }
    ]

    @patch("bitbucket_api._paginated_values")
    @patch("bitbucket_api.api_request")
    def test_reports_the_run_its_steps_and_the_log_follow_up(
            self, api_request, paginated_values):
        api_request.return_value = self.RUN
        paginated_values.return_value = self.STEPS
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_pipeline(self.CONFIG, "#1333")

        output = stdout.getvalue()
        self.assertIn("## Pipeline #1333 — COMPLETED/FAILED", output)
        self.assertIn("Branch: feature/IB-123 | Commit: abcdef123456", output)
        self.assertIn("Custom pipeline: deploy", output)
        self.assertIn("Trigger: PUSH | By: Ann Dev", output)
        self.assertIn("Duration: 4m 12s", output)
        self.assertIn(
            "URL: https://bitbucket.org/acme/widgets/pipelines/results/1333", output
        )
        # The full uuid, so a follow-up command resolves with no lookup.
        self.assertIn("Id: d4a04f6c-1111-2222-3333-444455556666", output)
        self.assertIn("Container 'build' exceeded memory limit.", output)
        self.assertIn(
            "pipeline-log d4a04f6c-1111-2222-3333-444455556666", output
        )
        paginated_values.assert_called_once_with(
            self.CONFIG,
            "/pipelines/{d4a04f6c-1111-2222-3333-444455556666}/steps/?pagelen=100",
        )

    @patch("bitbucket_api._paginated_values", return_value=[])
    @patch("bitbucket_api.api_request")
    def test_omits_the_log_follow_up_when_no_step_failed(
            self, api_request, paginated_values):
        api_request.return_value = dict(
            self.RUN, state={"name": "IN_PROGRESS", "stage": {"name": "PAUSED"}}
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cmd_pipeline(self.CONFIG, "1333")

        output = stdout.getvalue()
        self.assertIn("## Pipeline #1333 — IN_PROGRESS/PAUSED", output)
        self.assertIn("No steps found.", output)
        self.assertNotIn("pipeline-log", output)


class SinglePositionalArgumentsTest(unittest.TestCase):
    def test_accepts_exactly_one_identifier(self):
        self.assertEqual("42", _single_pr_id("get-pr", ["42"]))
        self.assertEqual(
            "d4a04f6c", _single_positional("pipeline-steps", ["d4a04f6c"], "PIPELINE_ID")
        )

    def test_rejects_missing_extra_and_non_numeric_identifiers(self):
        pr_id_cases = [
            [],
            ["--current-branch"],
            ["42", "43"],
            ["abc"],
            ["0"],
            ["https://bitbucket.org/acme/widgets/pull-requests/42"],
        ]
        for args in pr_id_cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _single_pr_id("get-pr", args)

        for args in ([], ["--full"], ["a", "b"]):
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _single_positional("pipeline-steps", args, "PIPELINE_ID")


class CommandDispatchTest(unittest.TestCase):
    """Every command must reject what it does not understand."""

    def _run(self, argv):
        stderr = io.StringIO()
        with patch("bitbucket_api.load_config",
                   return_value={"workspace": "acme", "repo_slug": "widgets"}), \
                patch("bitbucket_api.api_request") as api_request, \
                patch.object(sys, "argv", ["bitbucket_api.py"] + argv), \
                redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            try:
                main()
                code = 0
            except SystemExit as exit_error:
                code = exit_error.code
        return code, stderr.getvalue(), api_request.call_count

    def test_every_command_reports_an_unknown_flag_without_calling_the_api(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                code, stderr, api_calls = self._run([command, "--bogus-flag"])

                self.assertEqual(1, code)
                self.assertIn(command, stderr)
                self.assertNotIn("unknown command", stderr)
                self.assertEqual(0, api_calls)

    def test_unknown_command_is_reported_on_stderr(self):
        code, stderr, api_calls = self._run(["make-pr"])

        self.assertEqual(1, code)
        self.assertIn("unknown command: make-pr", stderr)
        self.assertIn("Usage:", stderr)
        self.assertEqual(0, api_calls)

    def test_help_prints_usage_and_succeeds(self):
        for flag in ("help", "--help", "-h"):
            with self.subTest(flag=flag):
                self.assertEqual(0, self._run([flag])[0])


class LogReadingTest(unittest.TestCase):
    def test_retains_only_requested_tail_while_counting_all_lines(self):
        total, lines = _read_log_lines(
            io.BytesIO(b"one\ntwo\nthree\nfour\n"), 2
        )

        self.assertEqual(4, total)
        self.assertEqual(["three", "four"], lines)

    def test_full_mode_retains_all_lines(self):
        total, lines = _read_log_lines(io.BytesIO(b"one\ntwo"), None)

        self.assertEqual(2, total)
        self.assertEqual(["one", "two"], lines)


class PipelineLogDownloadTest(unittest.TestCase):
    @patch("bitbucket_api.urllib.request.urlopen")
    @patch("bitbucket_api.urllib.request.build_opener")
    def test_reports_redirected_download_error_without_forwarding_auth(
            self, build_opener, urlopen):
        class RedirectResponse:
            status = 307
            headers = {"Location": "https://logs.example/presigned"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        opener = build_opener.return_value
        opener.open.return_value = RedirectResponse()
        urlopen.side_effect = urllib.error.HTTPError(
            "https://logs.example/presigned",
            403,
            "Forbidden",
            {},
            io.BytesIO(b"expired"),
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            _fetch_pipeline_log(
                {"workspace": "acme", "repo_slug": "widgets", "api_token": "secret"},
                "{pipeline}",
                "{step}",
                200,
            )

        first_request = opener.open.call_args[0][0]
        download_request = urlopen.call_args[0][0]
        self.assertEqual("Bearer secret", first_request.get_header("Authorization"))
        self.assertIsNone(download_request.get_header("Authorization"))
        self.assertIn("Error 403: expired", stderr.getvalue())


class PipelineLogArgumentsTest(unittest.TestCase):
    def test_parses_step_lines_and_full_options(self):
        self.assertEqual(
            ("d4a04f6c", "abc", 25),
            _parse_pipeline_log_args(["d4a04f6c", "abc", "--lines", "25"]),
        )
        self.assertEqual(
            ("d4a04f6c", None, None), _parse_pipeline_log_args(["d4a04f6c", "--full"])
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["--full"],
            ["p", "--lines"],
            ["p", "--lines", "--full"],
            ["p", "--lines", "many"],
            ["p", "--lines", "0"],
            ["p", "--lines", "-1"],
            ["p", "--unknown"],
            ["p", "first", "second"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_pipeline_log_args(args)


if __name__ == "__main__":
    unittest.main()

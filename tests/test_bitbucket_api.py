import io
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from bitbucket_api import (
    COMMANDS,
    _NoRedirect,
    _config_paths,
    _fetch_pipeline_log,
    _paginated_values,
    _parse_add_comment_args,
    _parse_create_pr_args,
    _parse_list_prs_args,
    _parse_merge_pr_args,
    _parse_pipeline_log_args,
    _parse_pipelines_args,
    _parse_update_pr_args,
    _read_log_lines,
    _single_positional,
    _single_pr_id,
    cmd_approve_pr,
    cmd_list_prs,
    cmd_pr_commits,
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
        self.assertEqual(("42", "merge_commit"), _parse_merge_pr_args(["42"]))
        self.assertEqual(
            ("42", "squash"), _parse_merge_pr_args(["42", "--strategy", "squash"])
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
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_merge_pr_args(args)


class AddCommentArgumentsTest(unittest.TestCase):
    def test_joins_the_remaining_words_into_the_comment(self):
        self.assertEqual(("42", "LGTM"), _parse_add_comment_args(["42", "LGTM"]))
        self.assertEqual(
            ("42", "ship it now"),
            _parse_add_comment_args(["42", "ship", "it", "now"]),
        )
        self.assertEqual(
            ("42", "--- a rule"), _parse_add_comment_args(["42", "---", "a", "rule"])
        )

    def test_rejects_invalid_arguments(self):
        cases = [
            [],
            ["42"],
            ["42", "   "],
            ["abc", "text"],
            ["--text", "hi"],
            ["42", "--file", "notes.md"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_add_comment_args(args)


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

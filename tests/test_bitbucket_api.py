import io
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from bitbucket_api import (
    _NoRedirect,
    _config_paths,
    _fetch_pipeline_log,
    _paginated_values,
    _parse_list_prs_args,
    _parse_pipeline_log_args,
    _read_log_lines,
    cmd_approve_pr,
    cmd_list_prs,
    cmd_pr_commits,
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
        self.assertEqual(("abc", 25), _parse_pipeline_log_args(["abc", "--lines", "25"]))
        self.assertEqual((None, None), _parse_pipeline_log_args(["--full"]))

    def test_rejects_invalid_arguments(self):
        cases = [
            ["--lines"],
            ["--lines", "many"],
            ["--lines", "0"],
            ["--lines", "-1"],
            ["--unknown"],
            ["first", "second"],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    _parse_pipeline_log_args(args)


if __name__ == "__main__":
    unittest.main()

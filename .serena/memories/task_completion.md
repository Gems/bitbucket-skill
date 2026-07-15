# Task completion

- Run `python3 -m py_compile bitbucket_api.py`.
- Run `bash -n scripts/bb-review.sh scripts/bb-comment.sh` when shell scripts change.
- Run `git diff --check`.
- No automated test suite or linter is currently configured; exercise affected command parsing/helpers with focused mocks where behavior changed.
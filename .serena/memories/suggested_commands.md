# Suggested commands

- CLI help: `python3 bitbucket_api.py` (requires config loading before usage is printed).
- Syntax validation: `python3 -m py_compile bitbucket_api.py`.
- Shell syntax validation: `bash -n scripts/bb-review.sh scripts/bb-comment.sh`.
- Review current changes: `git diff --check` and `git diff`.
- Search project: `rg <pattern>`; enumerate tracked-style files: `rg --files`.
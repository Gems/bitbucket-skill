# Tech stack

- Python CLI targets Python 3.6+ and intentionally uses the standard library; optional `certifi` improves TLS trust setup.
- POSIX shell helper scripts require `curl` and `jq`.
- Git supplies current-branch and Bitbucket workspace/repository auto-detection.
- No package manifest or dedicated test framework is present.
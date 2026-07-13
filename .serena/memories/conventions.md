# Conventions

- Keep the Python CLI dependency-light and compatible with Python 3.6+.
- Commands are `cmd_<name>` functions dispatched manually from `main`; API failures print a concise stderr message and exit 1.
- Bitbucket API paths are repository-relative and use `api_request`; config can omit workspace/repo and derive both from the current working directory's `origin` remote.
- CLI output is Markdown-oriented for direct use by an agent.
- `SKILL.md` is normative workflow guidance; keep examples and command dispatcher behavior synchronized.
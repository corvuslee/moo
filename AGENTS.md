# Project instructions

Target Linux i686 and Python 3.11. Use the installed interpreter and uv, not pip.
Manage dependencies in pyproject.toml and commit uv.lock when commits are requested.
Do not publish, create a GitHub repository, or push without explicit permission.

## Local verification

Verified on this machine: uv 0.13.8, Ruff 0.17.5, ty 0.0.105, Python 3.11.2.

```sh
uv sync --locked --python /usr/bin/python3
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv build
```

Run tests after changes. All checks must run locally. Keep runtime dependencies small
and compatible with i686. Never add credentials or real account task data to fixtures.

## Contracts

Task commands are one-shot and noninteractive. Only explicit moo auth may prompt.
No polling, workers, automatic task execution, or automatic request retries.
Keep single-line JSON and stable exit codes independent of the backend. Return
success objects directly and reserve top-level error for failures, without schema
versioning or success wrappers. No --human or --timeout options. Use a fixed
10-second operation budget shared across requests, excluding human approval wait.
RTM implementation belongs in src/moo/backends/rtm.py; contracts belong in contracts.py.
Preserve unrelated tags. Reject ambiguous recurring/completed task mutations.
Use narrow typed interfaces and owned output models. Never expose upstream exception
messages, shared secrets, or tokens. Never add source comments unless requested.

The inspected rtmilk synchronous transport had no configurable timeout and lacked move
coverage; moo currently uses a bounded standard-library HTTPS adapter instead.
Live account testing is opt-in and requires a disposable task and human authorization.

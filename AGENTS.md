# Working on moo

## Scope and constraints

moo is a command-line interface for Remember The Milk (RTM). Find tasks, read and
add notes, manage tags, and move tasks between lists.

Task commands must remain noninteractive. Only explicit `moo auth` may prompt.
Do not add polling, workers, automatic task execution, or automatic request retries.

Keep moo pure Python and CPU-architecture independent, supporting Python 3.11+.
Use the installed interpreter and uv, not pip. The package currently has no runtime
dependencies; keep new dependencies small and pure Python, without architecture-specific
native extensions. Manage dependencies in `pyproject.toml` and include `uv.lock`
when committing dependency changes.
Do not publish, create a GitHub repository, or push without explicit permission.
Never add source comments unless requested. Never add credentials or real account
task data to fixtures. Live account testing is opt-in and requires a disposable
task and human authorization.

## Commands and verification

Run from the repository root:

```sh
uv sync --locked --python /usr/bin/python3
uv run moo --help
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv build
```

Run tests after changes and all checks locally before handing off. The development
tools are pinned in `pyproject.toml`; the existing verified toolchain is uv 0.13.8,
Ruff 0.17.5, ty 0.0.105, and Python 3.11.2. Hatchling builds the package, and the
installed `moo` script calls `moo.cli:main`. Use `uv run moo` for local CLI work;
there is no `__main__.py` module entry point. If sync reports success but
`uv run moo` cannot spawn the executable, rebuilding the local editable install
restored it during verification:

```sh
uv sync --locked --python /usr/bin/python3 --reinstall-package moo
```

The deliberate catch-all in `src/moo/cli.py` implements the safe internal-error
output boundary and has a line-scoped `BLE001` suppression. Preserve that boundary
and keep the suppression limited to that handler.

`.github/workflows/ci.yml` runs the checks above for pushes and pull requests to
`main`, using Python 3.11 through 3.14 on Ubuntu and the pinned uv version.

For focused tests, use the existing test modules, for example:

```sh
uv run pytest tests/test_cli_contract.py
uv run pytest tests/test_rtm.py tests/test_backend_workflow.py
uv run pytest tests/test_auth.py
```

## Architecture and change boundaries

- `src/moo/cli.py` owns argument parsing, pre-backend validation, dispatch, JSON
  rendering, and exit handling. `run(argv, backend_factory)` is the test seam and
  returns an exit code; `main()` raises `SystemExit`. Validate references, IDs,
  tags, and nonempty note inputs before constructing a backend so invalid input
  cannot touch credentials or the network. Logout also bypasses backend creation.
- `src/moo/contracts.py` owns `MooError`, frozen output dataclasses, `TaskRef`, and
  the narrow `Backend` protocol. Keep output models independent of RTM response
  objects. The CLI serializes read models with `dataclasses.asdict`; mutations
  return dictionaries containing owned, normalized models.
- `src/moo/backends/rtm.py` owns all RTM protocol details: signing, bounded HTTPS,
  response normalization, error translation, read queries, and mutation safety.
  Public operations wrap their whole workflow in `RTMBackend.run`; internal
  helpers such as `_lists`, `_show`, and `_prepare` share that outer operation
  rather than starting a fresh budget.
- `src/moo/auth.py` owns credential precedence and storage, interactive approval,
  browser launching, and logout. Its `AuthBackend` protocol narrows the auth test
  seam. It imports the deadline primitive and budget from the RTM adapter.
- `docs/output-contract.md` is the detailed integration reference for result
  shapes, exit codes, model semantics, and uncertain writes. Keep it and CLI
  contract tests aligned when changing observable behavior. `README.md` covers
  installation and user-facing command examples.

Normal flow is parse/validate -> load credentials and construct RTM backend ->
dispatch -> operation-wide deadline -> RTM requests -> normalize owned models ->
one JSON result. Expected failures use `MooError(code, safe_message, exit_code,
details)`; the CLI suppresses unexpected exception text.

## Output invariants

- Write exactly one newline-terminated JSON object to stdout for success or failure,
  with Unicode escaped. Authentication guidance and approval URLs go to stderr.
  Help and version are plain-text exceptions.
- Return success objects directly; reserve top-level `error` for failures. Do not
  add schema versions, `ok`/`data` wrappers, `--human`, or `--timeout` options.
- Preserve backend-independent exit codes: 1 internal, 2 arguments, 3 config/auth,
  4 not found, 5 rejected state/operation, 6 transport/deadline, 7 upstream/response,
  and 130 cancellation. Omit empty error details, but retain meaningful false-valued
  fields inside a nonempty details object.
- IDs remain strings. Task references are `LIST:SERIES:TASK`, each component ASCII
  decimal digits. Completion is a timestamp or null, not a boolean. Dates and
  priorities pass through; do not promise result array ordering.
- Task listings exclude completed tasks by default. Repeated tag filters mean all
  requested tags, combined with any list restriction; tag matching happens locally
  after fetching normalized tasks.

## RTM transport and mutation gotchas

- Keep the standard-library HTTPS adapter. The previously inspected `rtmilk`
  synchronous transport lacked a configurable timeout and move coverage.
- The fixed 10-second budget spans all requests in an operation, not each request.
  `deadline()` uses `SIGALRM`/`ITIMER_REAL`, so this is Linux/main-thread code, not a
  thread-safe or portable timeout helper. Nested deadlines preserve the shorter
  outer timer and restore its remaining time. Request timeouts use the remaining
  timer budget as well.
- Interactive auth accounts for network phases before and after approval against
  one shared budget, excluding human approval wait. Do not wrap the entire prompt
  in a deadline or reset the budget after approval. Browser launching is optional
  and bounded separately.
- Requests are signed POSTs. RTM's signature hashes sorted, unescaped UTF-8
  parameter names/values prefixed with the shared secret, before URL encoding.
  Redirect following is disabled and response bodies are capped at 8,000,000 bytes.
  Never expose upstream exception messages, shared secrets, or tokens.
- RTM collections can be absent, empty, singleton objects, or arrays. `items()` and
  `normalize_tasks()` handle these shapes, including tag/note variants, omit deleted
  tasks, and preserve recurrence. Extend normalization and malformed-response tests
  rather than returning raw upstream dictionaries.
- `_prepare()` verifies a writable source list and exact task reference, rejects
  recurring/completed tasks, then creates a timeline. Smart or archived lists are
  not writable. Moves validate the destination before preparation; keep unsafe
  operations from reaching timeline creation or mutation calls.
- Use additive `tasks.addTags`/`tasks.removeTags` endpoints to preserve unrelated
  tags, not whole-tag replacement. A move sends `from_list_id` and `to_list_id`,
  verifies the normalized destination result, and returns a new reference; callers
  must not rely on the old reference afterward.
- Mark task-changing calls with `mutation=True`. `write_attempted` is reset once per
  operation and tracks uncertainty across later failures. Transport, HTTP, timeout,
  oversized, or malformed results after an attempted write may mean it succeeded;
  preserve the recovery guidance to inspect current state before retrying. Do not
  add a separate uncertainty flag or retry automatically. These operations are not
  transactions, task locks, or atomic claims.
- RTM requires account-wide `delete` permission for list moves. Authorization
  deliberately requests it even though moo has no delete command.

## Credential safety

Config path precedence is `MOO_CONFIG`, then
`$XDG_CONFIG_HOME/moo/credentials.json`, then
`~/.config/moo/credentials.json`. `RTM_API_KEY`, `RTM_SHARED_SECRET`, and
`RTM_AUTH_TOKEN` override saved values. A saved token is reused only if the selected
application key and secret still match its saved application; changing either must
not silently reuse that token. Invalid config is an error, not silently ignored.

Writes use a same-directory temporary file, mode 0600, flush/fsync, and atomic
replacement. Newly created config directories use 0700; existing directory modes
are intentionally left unchanged. Preserve old credentials on failed writes or
cancelled auth. Existing tokens are checked rather than silently replaced unless
`--force` is supplied. Even `auth --no-browser` needs a TTY; use `auth status` for
noninteractive checks. Logout removes only the saved token, preserving application
credentials; it does not unset environment variables or revoke RTM access.

## Testing patterns

The suite is offline and uses pytest, `unittest.mock`, `monkeypatch`, and synthetic
RTM responses. No live account is needed.

- `tests/test_cli_contract.py` injects a stateful fake backend through `run` and
  checks complete workflows, exact result fields, one-line stdout/stderr behavior,
  safe errors, and validation before backend construction.
- `tests/test_rtm.py` mocks the opener/context-manager response to exercise signed
  requests, normalization, error mappings, no retries, mutation validation, and
  uncertain writes. Its `backend` fixture disables the real signal deadline;
  passing these tests alone does not verify wall-clock limits.
- `tests/test_backend_workflow.py` exercises real short signal deadlines, remaining
  request budgets, nested timer restoration, additive tag endpoints, and auth
  permission requirements. Keep real deadline coverage when changing timeout code.
- `tests/test_auth.py` uses an autouse fixture that clears RTM/browser environment
  variables and redirects `MOO_CONFIG` into `tmp_path`. It covers private atomic
  storage, credential precedence, TTY/cancellation behavior, bounded browser launch,
  and shared network budgets. Use the same isolation for new credential tests.

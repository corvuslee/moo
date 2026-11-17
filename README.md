# moo

A one-shot Remember The Milk CLI for coding agents. Find tasks, read notes, record progress,
change tags, and move tasks into a review list. It never executes task contents, polls,
claims ownership, creates follow-up work, or automatically picks another task.

## Local setup

Verified locally on Linux i686 with Python 3.11.2 and uv 0.13.8. Ruff 0.17.5 and ty
0.0.105 both run on this machine. Runtime uses only the Python standard library.

```sh
uv sync --locked --python /usr/bin/python3
uv run moo --help
```

Keep using the installed Python 3.11 interpreter; no managed Python download is needed.
The package requires Python 3.11 or newer, but 3.11 is the tested development target.

## Authentication

Obtain an RTM application API key and shared secret. These identify your application;
account access also requires an authorized token. Set your application credentials:

```sh
export RTM_API_KEY='your-application-key'
export RTM_SHARED_SECRET='your-application-secret'
uv run moo auth
```

`moo auth` prints a signed authorization URL to stderr, attempts to open a graphical
browser, and waits for Enter after you approve access. It then exchanges the
pending authorization for a token and saves the credentials. It does not run a callback
server or poll. Ctrl-C cancels without overwriting existing credentials.

For a headless machine, run `uv run moo auth --no-browser` in a terminal and open the
printed URL in a browser on another machine. Setup requires an interactive terminal;
all task commands and `auth status` are noninteractive.

```sh
uv run moo auth status
uv run moo auth --force
uv run moo auth logout
```

Tokens have no documented automatic expiration, but access can be revoked or invalidated.
Invalid authorization produces an error instead of starting browser setup. `logout`
removes only the saved local token, not RTM's authorization or environment variables.
Revoke access separately in RTM if needed.

Credentials are saved atomically with mode 0600 in
`$XDG_CONFIG_HOME/moo/credentials.json` (default `~/.config/moo/credentials.json`). Newly
created application directories use mode 0700. Override the path with `MOO_CONFIG`.
Never store credentials in this repository. Environment variables override saved values:
`RTM_API_KEY`, `RTM_SHARED_SECRET`, `RTM_AUTH_TOKEN`. A saved token is not reused if the
application key or secret changes. An existing matching token avoids browser setup.

RTM requires its broad `delete` permission for `tasks.moveTo`, even though moving is
not deleting. Setup therefore requests `delete` permission; the CLI itself exposes no
delete command. Authorization is account-wide, not limited to selected lists or tags.
Queue filters are workflow conventions, not a security boundary.

## Agent workflow

Global options precede the command: `--human` and `--timeout SECONDS`.
Use the exact task reference returned by discovery, formatted `LIST:SERIES:TASK`.
Lists are selected by numeric IDs, not names. RTM labels are called tags.

```sh
uv run moo lists list
uv run moo tasks list --list-id 123 --tag agent-ready
uv run moo tasks show 123:456:789
uv run moo tasks tags add 123:456:789 --tag in-progress
uv run moo tasks tags remove 123:456:789 --tag agent-ready
uv run moo tasks notes add 123:456:789 --title 'Progress' --text 'Implemented; tests pass.'
uv run moo tasks tags add 123:456:789 --tag needs-review
uv run moo tasks tags remove 123:456:789 --tag in-progress
uv run moo tasks move 123:456:789 --to-list-id 321
uv run moo tasks show 321:456:789
```

These are illustrative IDs. Each line is a separate invocation; the CLI does not run
this sequence automatically. A move returns the updated reference. Rediscover tasks
if a reference becomes stale. Adding/removing tags preserves unrelated tags.

Task discovery defaults to incomplete tasks. `--include-completed` explicitly broadens
results. List and tag selectors combine with AND; repeated `--tag` requires every tag.
Tag comparisons are exact. Tags cannot contain whitespace or commas. Empty results
are successful. Notes are RTM task notes, not separate conversation messages.

Choose a queue convention: only tasks with `agent-ready` enter work; remove that tag
when starting, and use a separate review list and/or `needs-review` tag for handoff.
There is no atomic claim, lock, multi-step transaction, or automatic transition. Two
agents can discover the same task. The caller coordinates concurrent work and partial
transitions. Discovery never changes tasks.

Mutations of recurring/completed tasks and smart/archived list targets are intentionally
rejected in this version to avoid ambiguous series changes. Task creation, deletion,
completion/reopening, and editing names/dates/priorities are not implemented.

## Output and failure contract

JSON is the default, one envelope per invocation:

```json
{"schema_version": 1, "ok": true, "data": {"tasks": []}}
```

```json
{"schema_version": 1, "ok": false, "error": {"code": "not_found", "message": "Task reference not found; rediscover after moving a task.", "details": {}}}
```

Results and structured errors go to stdout. Auth prompts and authorization URLs go to
stderr. Help/version are conventional text. `--human` renders indented result data or
concise error text instead of the envelope. Never parse that mode in an agent.

Tasks expose `ref`, `list_id`, `series_id`, `task_id`, `name`, `tags`, `notes`, `completed`,
`due`, `priority`, and `recurring`. IDs are strings, collections are arrays, absent dates
are null. Notes expose `id`, `title`, `text`, `created`, and `modified`.

| Exit | Meaning |
| --- | --- |
| 0 | Success, including empty results |
| 1 | Unexpected internal failure |
| 2 | Invalid arguments |
| 3 | Configuration/authentication failure |
| 4 | Task/list not found |
| 5 | Rejected operation or unsupported state |
| 6 | Transport failure, timeout, or uncertain write |
| 7 | Rate limit, upstream HTTP failure, invalid/oversized response |
| 130 | Cancelled |

No requests are automatically retried. Each task operation has a wall-clock deadline
(default 30 seconds, maximum 300), covering all preflight requests and DNS resolution
on the supported Linux main-thread CLI. Auth's two network phases each have that bound;
the human confirmation wait is intentionally unbounded. Responses are limited to 8 MB.
Redirects are not followed. Credentials are sent over HTTPS in signed POST bodies.

An interrupted write can have succeeded remotely. Inspect `write_may_have_applied` in
error details, then read current state before retrying, especially before adding another
note. An upstream HTTP error is not a guarantee that a write was not applied.

## Architecture and backend choice

`cli.py` owns commands, rendering, and exits. `contracts.py` owns task models, references,
errors, and the backend protocol. `auth.py` handles guided setup and private storage.
`backends/rtm.py` is the only RTM-specific adapter and normalizes upstream responses.

We inspected the installed `rtmilk` implementation: its synchronous transport calls
`niquests.get` without a configurable timeout, and its low-level endpoint coverage lacks
the move operation needed here. Rather than patch private internals, this version uses
a narrow standard-library HTTP adapter. The backend can be replaced without changing
the CLI contract. No task text or backend exception details are logged.

## Local checks

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv build
```

Tests use fake backends and mocked HTTP responses, not live account data. A live smoke
test must be explicitly performed with your credentials and a disposable, non-recurring
task: authenticate, discover its list/reference, inspect, add a note, add/remove a tag,
move to another regular list, then inspect using the returned reference. This has not
been run without account credentials. No GitHub repository or publication is required.

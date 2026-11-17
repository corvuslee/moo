# Output contract

This document describes moo's output contract for AI agents interacting with
Remember The Milk.

## Streams and encoding

Each completed command invocation writes one JSON object on one line
to **stdout**, followed by a newline. Both successful results and structured errors
use stdout. Non-ASCII characters are escaped using JSON Unicode escapes.

Authentication instructions and authorization URLs go to **stderr**. `moo auth`
requires an interactive terminal and may wait for confirmation; task commands,
`auth status`, and `auth logout` do not prompt.

Help and version output are plain text, not JSON. An externally killed process is
not guaranteed to produce a result.

## Result shape

Success returns the command-specific object directly, with exit code 0:

```json
{"tasks": []}
```

Failure returns an `error` object, with a nonzero exit code:

```json
{"error": {"code": "not_found", "message": "List not found."}}
```

- There is no `schema_version`, `ok`, or `data` wrapper.
- `error` is reserved for failures and never appears as a top-level success field.
  Agents can recognize failure from this field without inspecting the exit code.
- Success fields are command-specific, described below.
- `error.code` is a machine-readable string; branch on it rather than the message.
- `error.message` is explanatory text, not a stable parsing interface.
- `error.details` is included only when it is a nonempty object. Empty or absent
  details are omitted; meaningful values, including false-valued fields, are
  preserved.

Consumers should tolerate unknown object fields. This document does not establish
a separate release compatibility policy.

## Command results

The following shapes describe the complete success object written to stdout.
`Task`, `Note`, and `List` refer to the models below.

| Command | Result |
| --- | --- |
| `moo lists list` | `{"lists": [List, ...]}` |
| `moo tasks list` | `{"tasks": [Task, ...]}` |
| `moo tasks show REF` | `{"task": Task}` |
| `moo tasks notes add REF ...` | `{"ref": "LIST:SERIES:TASK", "note": Note}` |
| `moo tasks tags add REF ...` | `{"task": Task}` containing the updated task |
| `moo tasks tags remove REF ...` | `{"task": Task}` containing the updated task |
| `moo tasks move REF --to-list-id ID` | `{"ref": "NEW_LIST:SERIES:TASK", "previous_ref": "LIST:SERIES:TASK"}` |
| `moo auth` after new authorization | `{"authenticated": true, "saved": true}` |
| `moo auth` with a valid saved or configured token | Same result as `auth status` |
| `moo auth status` | `{"authorized": true, "permissions": "delete", "username": "example", "user_id": "123"}` |
| `moo auth logout` | `{"logged_out": true, "token_removed": true, "message": "..."}` |

Empty list/task results are successful and use empty arrays. Task listings exclude
completed tasks unless `--include-completed` is supplied. Repeated `--tag` filters
require every tag, and combine with `--list-id` using AND.

A move returns the new reference; use it for subsequent commands. Do not assume the
old reference remains valid. Adding or removing tags preserves unrelated tags.

Authentication status fields `permissions`, `username`, and `user_id` are passed
through from RTM and may be null when omitted upstream. The example above shows a
typical response, not fixed account values. Status failure returns an error, not
`authorized: false`. Credentials and authorization tokens are not included in results.

For logout, `token_removed` indicates whether a nonempty saved token was removed.
Logout does not clear environment variables or revoke access in Remember The Milk.

## Models

### List

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | List ID |
| `name` | string | List name |
| `smart` | boolean | Whether this is a smart list |
| `archived` | boolean | Whether this list is archived |

### Task

| Field | Type | Meaning |
| --- | --- | --- |
| `ref` | string | Reference formatted `LIST:SERIES:TASK` |
| `list_id` | string | List ID |
| `series_id` | string | Task series ID |
| `task_id` | string | Individual task ID |
| `name` | string | Task name |
| `tags` | array of strings | Task tags, or `[]` |
| `notes` | array of Note objects | Task notes, or `[]` |
| `completed` | string or null | Completion timestamp, not a boolean |
| `due` | string or null | Due date/time returned by RTM |
| `priority` | string or null | Priority value returned by RTM |
| `recurring` | boolean | Whether the task is recurring |

IDs are strings, not JSON numbers. Task reference components must contain ASCII
decimal digits. Dates and priority values are passed through rather than converted
into local dates or numeric priorities. Do not depend on array ordering.

### Note

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Note ID |
| `title` | string | Note title |
| `text` | string | Note contents |
| `created` | string or null | Creation timestamp |
| `modified` | string or null | Last modification timestamp |

## Errors and exit codes

| Exit | Meaning | Current error codes |
| --- | --- | --- |
| 0 | Success, including empty results, help, and version | None |
| 1 | Unexpected internal failure | `internal_error` |
| 2 | Invalid command arguments | `invalid_arguments` |
| 3 | Configuration or authentication failure | `config_error`, `authentication_required`, `authentication_failed`, `auth_requires_tty`, `auth_error` |
| 4 | Task or list not found | `not_found` |
| 5 | Rejected operation or unsupported state | `invalid_state`, `operation_rejected` |
| 6 | Transport failure or operation timeout | `transport_error`, `deadline_exceeded` |
| 7 | Upstream HTTP failure or invalid response | `rate_limited`, `upstream_error`, `invalid_response`, `response_too_large` |
| 130 | Cancelled | `cancelled` |

For example, changes to recurring/completed tasks or smart/archived lists are
rejected with `invalid_state`. Upstream exception text is not exposed in unexpected
internal errors.

Depending on the failure, `details` can include:

| Field | Type | Meaning |
| --- | --- | --- |
| `api_code` | string | RTM API error code |
| `http_status` | integer | Upstream HTTP status |

## Timeouts, retries, and uncertain writes

moo never automatically retries a request. Commands have a fixed 10-second total
operation deadline, shared across all requests, not 10 seconds per request. There
is no `--timeout` option. Exceeding the deadline returns `deadline_exceeded` with
exit code 6.

For interactive authentication, waiting for human approval is excluded from the
10-second operation budget; network work before and after approval shares that
budget.

A failed command does **not** necessarily mean a change was rolled back. If a
mutation was attempted and its outcome cannot be confirmed because of a transport
or HTTP failure, an invalid or oversized response, a timeout, or an unexpected
response shape, `error.message` explains how to recover:

> Could not confirm the operation. It may have succeeded. Check the task's current state before retrying.

Inspect the task before retrying, particularly before adding another note. There
is no separate uncertainty flag in the output. Errors with no other details omit
`error.details`; HTTP failures still include `http_status`. Read-only failures
retain their failure-specific messages. The message is guidance for recovery,
not a stable parsing interface; continue to branch on `error.code`. A different
message or absent details is not proof that no change occurred.

If a move may have succeeded, rediscover the task in the destination list rather
than relying on its previous reference. Commands do not provide a multi-command
transaction, task lock, or atomic claim.

## Single output format

There is no `--human` option or alternative result rendering mode. Success and
failure results use the single-line JSON format above. Help and version remain
plain-text exceptions.

## Implementation references

- `src/moo/contracts.py`: models, task references, and backend interface.
- `src/moo/cli.py`: command results, JSON rendering, and exit handling.
- `src/moo/backends/rtm.py`: RTM result normalization and failure mapping.
- `src/moo/auth.py`: authorization and logout results.
- `tests/test_cli_contract.py`: CLI contract tests.

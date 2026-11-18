# moo

A command-line interface for Remember The Milk. Find tasks, read and add notes,
and manage tags.

## Installation

Requires Python 3.11+ and uv.

```sh
uv tool install git+https://github.com/corvuslee/moo.git
moo --help
```

## Authentication

Get an application API key and shared secret from
[Remember The Milk](https://www.rememberthemilk.com/services/api/keys.rtm), then run:

```sh
export RTM_API_KEY='your-application-key'
export RTM_SHARED_SECRET='your-application-secret'
moo auth
```

Approve access in your browser, then press Enter. For a remote or headless machine,
use `moo auth --no-browser` and open the printed link in a browser.

Credentials are saved in `~/.config/moo/credentials.json` (or
`$XDG_CONFIG_HOME/moo/credentials.json`). Use `moo auth status` to check access and
`moo auth logout` to remove the saved token. Logging out does not revoke access in
Remember The Milk.

### Upgrading to 0.2.0

`moo tasks move` has been removed. New authorizations request account-wide
`write` permission instead of `delete`. Existing `delete` tokens are not
automatically downgraded. To reduce access, revoke the old authorization in
Remember The Milk, then run `moo auth --force` to authorize again.

If you use `RTM_AUTH_TOKEN`, replace it with the new token or unset it to use
moo's newly saved token. An environment token overrides the saved token.

## Usage

Use `moo --help-all` for the complete command reference, or `moo <subcommand> --help`
for a subcommand's commands and arguments.

```sh
# Show lists and their IDs
moo lists list

# Show incomplete tasks in a list (replace example IDs with your own)
moo tasks list --list-id 123

# Include completed tasks
moo tasks list --list-id 123 --include-completed

# Filter tasks by tag
moo tasks list --list-id 123 --tag waiting

# Show a task and its notes using its returned reference
moo tasks show 123:456:789

# Add a note to a task
moo tasks notes add 123:456:789 --title 'Update' --text 'Waiting for feedback.'

# Add or remove a tag
moo tasks tags add 123:456:789 --tag waiting
moo tasks tags remove 123:456:789 --tag waiting
```

Commands return single-line JSON with a fixed 10-second operation deadline and no
automatic retries. For agents and integrations, see the
[output contract](docs/output-contract.md).

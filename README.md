# moo

A command-line interface for Remember The Milk. Find tasks, read and add notes,
manage tags, and move tasks between lists.

> **Permissions:** During setup, moo requests Remember The Milk's account-wide
> “delete” permission because RTM requires it to move tasks between lists.
> The CLI has no delete command.

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

## Usage

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

# Move a task to another list and inspect its updated reference
moo tasks move 123:456:789 --to-list-id 321
moo tasks show 321:456:789
```

Commands return single-line JSON with a fixed 10-second operation deadline and no
automatic retries. For agents and integrations, see the
[output contract](docs/output-contract.md).

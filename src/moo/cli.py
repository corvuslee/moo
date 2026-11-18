from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Never

from moo.auth import authenticate, load_credentials, logout
from moo.backends.rtm import RTMBackend
from moo.contracts import Backend, MooError, TaskRef


class ReferenceHelpFormatter(argparse.RawTextHelpFormatter):
    def __init__(self, prog: str) -> None:
        super().__init__(prog, width=sys.maxsize)


class HelpAll(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[str] | None,
        option_string: str | None = None,
    ) -> None:
        if isinstance(parser, Parser):
            parser._print_message(parser.format_help_all(), sys.stdout)
        parser.exit()


class Parser(argparse.ArgumentParser):
    def format_help_all(self) -> str:
        sections = [self.format_help()]
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    if isinstance(child, Parser):
                        sections.append(
                            f"\n=== {child.prog} ===\n{child.format_help_all()}"
                        )
        return "".join(sections)

    def error(self, message: str) -> Never:
        raise MooError("invalid_arguments", message, 2)


def identifier(value: str) -> str:
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("IDs must contain ASCII digits only")
    return value


def tag_value(value: str) -> str:
    if not value or any(character.isspace() or character == "," for character in value):
        raise argparse.ArgumentTypeError(
            "Tags must be nonempty and contain no whitespace or commas"
        )
    return value


def parser() -> Parser:
    root = Parser(
        prog="moo",
        formatter_class=ReferenceHelpFormatter,
        description="A command-line interface for Remember The Milk.",
        epilog="Use <subcommand> --help for command help or --help-all for the complete reference.",
    )
    root.add_argument(
        "--help-all",
        action=HelpAll,
        nargs=0,
        help="Show the complete command reference, then exit",
    )
    root.add_argument("--version", action="version", version="moo 0.2.0")
    commands = root.add_subparsers(dest="command", required=True, title="commands")

    def command(parent, name, description, **kwargs):
        kwargs["formatter_class"] = ReferenceHelpFormatter
        if parent is commands:
            child = parent.add_parser(
                name,
                help=description,
                description=description,
                add_help=False,
                **kwargs,
            )
            child.add_argument(
                "-h",
                "--help",
                action=HelpAll,
                nargs=0,
                help="Show help for this command and all its subcommands, then exit",
            )
            return child
        return parent.add_parser(
            name, help=description, description=description, **kwargs
        )

    auth = command(
        commands,
        "auth",
        "Authorize interactively using your browser, or manage authorization.",
        epilog=(
            "Requires a TTY, RTM_API_KEY and RTM_SHARED_SECRET (or saved application "
            "credentials). Requests account-wide write permission for notes and tags. "
            "Use auth status for a noninteractive access check."
        ),
    )
    auth.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the approval URL without opening a browser; still requires a TTY",
    )
    auth.add_argument(
        "--force",
        action="store_true",
        help="Request new authorization instead of checking an existing token",
    )
    auth_actions = auth.add_subparsers(dest="auth_action", title="commands")
    command(
        auth_actions,
        "status",
        "Check authorization without prompting; returns authorized and account metadata.",
    )
    command(
        auth_actions,
        "logout",
        "Remove only the saved token; does not revoke access or clear environment variables.",
    )
    lists = command(
        commands, "lists", "Discover Remember The Milk lists and their IDs."
    )
    command(
        lists.add_subparsers(dest="action", required=True, title="commands"),
        "list",
        'Return lists with string IDs, names, smart and archived flags as {"lists": [...]}.',
    )
    tasks = command(
        commands, "tasks", "Find, inspect and update tasks without prompting."
    )
    task_actions = tasks.add_subparsers(dest="action", required=True, title="commands")
    listing = command(
        task_actions,
        "list",
        'Return matching tasks as {"tasks": [...]}; excludes completed tasks by default.',
    )
    listing.add_argument(
        "--list-id", type=identifier, help="Restrict to a list ID (ASCII digits)"
    )
    listing.add_argument(
        "--tag",
        action="append",
        type=tag_value,
        default=[],
        help="Require this tag; repeat to require all tags, combined with --list-id",
    )
    listing.add_argument(
        "--include-completed", action="store_true", help="Include completed tasks"
    )
    reference_help = (
        "Task reference LIST:SERIES:TASK, each component containing ASCII digits"
    )
    mutation_help = "Changes require an incomplete, nonrecurring task in a writable (not smart or archived) list."
    show = command(
        task_actions,
        "show",
        'Return a task including its tags and notes as {"task": {...}}.',
    )
    show.add_argument("ref", help=reference_help)
    notes = command(task_actions, "notes", "Manage task notes.")
    note = command(
        notes.add_subparsers(dest="note_action", required=True, title="commands"),
        "add",
        "Add a note; returns ref and the created note.",
        epilog=mutation_help,
    )
    note.add_argument("ref", help=reference_help)
    note.add_argument(
        "--title",
        required=True,
        help="Nonempty note title (quote text containing spaces)",
    )
    note.add_argument(
        "--text",
        required=True,
        help="Nonempty note body (quote text containing spaces)",
    )
    tags = command(
        task_actions, "tags", "Add or remove tags while preserving unrelated tags."
    )
    tag_actions = tags.add_subparsers(
        dest="tag_action", required=True, title="commands"
    )
    for name in ("add", "remove"):
        tag = command(
            tag_actions,
            name,
            f'{name.capitalize()} tags; returns the updated task as {{"task": {{...}}}}.',
            epilog=mutation_help,
        )
        tag.add_argument("ref", help=reference_help)
        tag.add_argument(
            "--tag",
            action="append",
            type=tag_value,
            required=True,
            help="Tag to change; repeat for multiple tags; no whitespace or commas",
        )
    return root


def make_backend(args: argparse.Namespace) -> Backend:
    credentials = load_credentials(require_token=args.command != "auth")
    return RTMBackend(credentials.api_key, credentials.shared_secret, credentials.token)


def dispatch(args: argparse.Namespace, backend: Backend) -> dict[str, object]:
    if args.command == "auth":
        if args.auth_action == "status":
            return backend.check_token()
        return authenticate(
            load_credentials(require_token=False),
            backend,
            force=args.force,
            no_browser=args.no_browser,
        )
    if args.command == "lists":
        return {"lists": [asdict(value) for value in backend.lists()]}
    if args.action == "list":
        return {
            "tasks": [
                asdict(value)
                for value in backend.tasks(
                    args.list_id, args.tag, args.include_completed
                )
            ]
        }
    ref = TaskRef.parse(args.ref)
    if args.action == "show":
        return {"task": asdict(backend.show(ref))}
    if args.action == "notes":
        return backend.add_note(ref, args.title, args.text)
    return backend.change_tags(ref, args.tag, remove=args.tag_action == "remove")


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True))


def run(
    argv: Sequence[str] | None = None,
    backend_factory: Callable[[argparse.Namespace], Backend] = make_backend,
) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = parser().parse_args(arguments)
        if args.command == "auth" and args.auth_action == "logout":
            data = logout()
        else:
            if hasattr(args, "ref"):
                TaskRef.parse(args.ref)
            if getattr(args, "action", None) == "notes" and (
                not args.title.strip() or not args.text.strip()
            ):
                raise MooError(
                    "invalid_arguments", "Note title and text must be nonempty.", 2
                )
            data = dispatch(args, backend_factory(args))
        emit(data)
        return 0
    except MooError as error:
        emit(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    **({"details": error.details} if error.details else {}),
                },
            },
        )
        return error.exit_code
    except (KeyboardInterrupt, EOFError):
        emit(
            {
                "error": {
                    "code": "cancelled",
                    "message": "Operation cancelled.",
                },
            },
        )
        return 130
    except Exception:  # noqa: BLE001
        emit(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Unexpected internal failure.",
                },
            },
        )
        return 1


def main() -> None:
    raise SystemExit(run())

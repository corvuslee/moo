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


class Parser(argparse.ArgumentParser):
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
        prog="moo", description="A command-line interface for Remember The Milk."
    )
    root.add_argument("--version", action="version", version="moo 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="Authorize once using your browser")
    auth.add_argument("--no-browser", action="store_true")
    auth.add_argument("--force", action="store_true")
    auth_actions = auth.add_subparsers(dest="auth_action")
    auth_actions.add_parser("status")
    auth_actions.add_parser("logout")
    lists = commands.add_parser("lists")
    lists.add_subparsers(dest="action", required=True).add_parser("list")
    tasks = commands.add_parser("tasks")
    task_actions = tasks.add_subparsers(dest="action", required=True)
    listing = task_actions.add_parser("list")
    listing.add_argument("--list-id", type=identifier)
    listing.add_argument("--tag", action="append", type=tag_value, default=[])
    listing.add_argument("--include-completed", action="store_true")
    show = task_actions.add_parser("show")
    show.add_argument("ref")
    notes = task_actions.add_parser("notes")
    note = notes.add_subparsers(dest="note_action", required=True).add_parser("add")
    note.add_argument("ref")
    note.add_argument("--title", required=True)
    note.add_argument("--text", required=True)
    tags = task_actions.add_parser("tags")
    tag_actions = tags.add_subparsers(dest="tag_action", required=True)
    for name in ("add", "remove"):
        tag = tag_actions.add_parser(name)
        tag.add_argument("ref")
        tag.add_argument("--tag", action="append", type=tag_value, required=True)
    move = task_actions.add_parser("move")
    move.add_argument("ref")
    move.add_argument("--to-list-id", required=True, type=identifier)
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
    if args.action == "tags":
        return backend.change_tags(ref, args.tag, remove=args.tag_action == "remove")
    return backend.move(ref, args.to_list_id)


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
    except Exception:
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

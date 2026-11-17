import json
from dataclasses import asdict, replace

import pytest

from moo.cli import run
from moo.contracts import MooError, Note, Task, TaskList, TaskRef


class FakeBackend:
    def __init__(self):
        self.task = Task("1:2:3", "1", "2", "3", "Implement feature", ["agent-ready"])

    def lists(self):
        return [
            TaskList("1", "Agent", False, False),
            TaskList("4", "Review", False, False),
        ]

    def tasks(self, list_id, tags, include_completed):
        return [self.task] if set(tags).issubset(self.task.tags) else []

    def show(self, ref):
        if str(ref) != self.task.ref:
            raise MooError("not_found", "Task not found.", 4)
        return self.task

    def change_tags(self, ref, tags, remove):
        values = set(self.task.tags)
        values.difference_update(tags) if remove else values.update(tags)
        self.task = Task(
            str(ref),
            ref.list_id,
            ref.series_id,
            ref.task_id,
            self.task.name,
            sorted(values),
            self.task.notes,
        )
        return {"task": asdict(self.task)}

    def add_note(self, ref, title, text):
        self.task.notes.append(Note("8", title, text))
        return {"ref": str(ref), "note": asdict(self.task.notes[-1])}

    def move(self, ref, destination):
        new_ref = TaskRef(destination, ref.series_id, ref.task_id)
        self.task = Task(
            str(new_ref),
            destination,
            ref.series_id,
            ref.task_id,
            self.task.name,
            self.task.tags,
            self.task.notes,
        )
        return {"ref": str(new_ref), "previous_ref": str(ref)}

    def check_token(self):
        return {"authorized": True}

    def auth_url(self):
        return "unused"

    def exchange_token(self):
        return "unused"


def invoke(arguments, backend, capsys):
    code = run(arguments, lambda args: backend)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert len(captured.out.splitlines()) == 1
    return code, json.loads(captured.out)


def test_agent_workflow(capsys):
    backend = FakeBackend()
    for command, fields in [
        (["lists", "list"], {"lists"}),
        (["tasks", "list", "--list-id", "1", "--tag", "agent-ready"], {"tasks"}),
        (["tasks", "show", "1:2:3"], {"task"}),
        (["tasks", "tags", "add", "1:2:3", "--tag", "in-progress"], {"task"}),
        (["tasks", "tags", "remove", "1:2:3", "--tag", "agent-ready"], {"task"}),
        (
            [
                "tasks",
                "notes",
                "add",
                "1:2:3",
                "--title",
                "Progress",
                "--text",
                "Tests pass",
            ],
            {"ref", "note"},
        ),
        (["tasks", "tags", "add", "1:2:3", "--tag", "needs-review"], {"task"}),
        (["tasks", "move", "1:2:3", "--to-list-id", "4"], {"ref", "previous_ref"}),
    ]:
        code, body = invoke(command, backend, capsys)
        assert code == 0
        assert set(body) == fields
    code, body = invoke(["tasks", "list", "--tag", "agent-ready"], backend, capsys)
    assert code == 0
    assert body["tasks"] == []
    code, body = invoke(["tasks", "show", "4:2:3"], backend, capsys)
    assert body["task"]["notes"][0]["text"] == "Tests pass"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["watch"],
        ["tasks", "create"],
        ["tasks", "show", "bad"],
        ["tasks", "list", "--list-id", "not-an-id"],
        ["tasks", "list", "--tag", "two,tags"],
        ["--human", "lists", "list"],
        ["--timeout", "10", "lists", "list"],
        ["tasks", "notes", "add", "1:2:3", "--title", "", "--text", "value"],
    ],
)
def test_invalid_arguments_never_construct_backend(arguments, capsys):
    def fail(args):
        pytest.fail("Backend must not be constructed")

    assert run(arguments, fail) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    ("code", "details"),
    [
        (3, None),
        (4, {}),
        (5, {"retryable": False}),
        (6, None),
        (7, {"http_status": 503}),
    ],
)
def test_error_contract(code, details, capsys):
    def fail(args):
        raise MooError("failure", "Safe message", code, details)

    assert run(["lists", "list"], fail) == code
    body = json.loads(capsys.readouterr().out)
    expected = {"code": "failure", "message": "Safe message"}
    if details:
        expected["details"] = details
    assert body == {"error": expected}


@pytest.mark.parametrize(
    ("failure", "exit_code", "code", "message"),
    [
        (
            RuntimeError("secret token"),
            1,
            "internal_error",
            "Unexpected internal failure.",
        ),
        (KeyboardInterrupt(), 130, "cancelled", "Operation cancelled."),
        (EOFError(), 130, "cancelled", "Operation cancelled."),
    ],
)
def test_safe_error_results(failure, exit_code, code, message, capsys):
    def fail(args):
        raise failure

    assert run(["lists", "list"], fail) == exit_code
    assert json.loads(capsys.readouterr().out) == {
        "error": {"code": code, "message": message}
    }


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_help_and_version_remain_plain_text(option, capsys):
    with pytest.raises(SystemExit) as caught:
        run([option])
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "moo" in captured.out
    assert not captured.out.startswith("{")
    if option == "--help":
        assert "A command-line interface for Remember The Milk." in captured.out


HELP_COMMANDS = [
    ([], "Remember The Milk"),
    (["auth"], "--no-browser"),
    (["auth", "status"], "without prompting"),
    (["auth", "logout"], "saved token"),
    (["lists"], "Discover"),
    (["lists", "list"], "string IDs"),
    (["tasks"], "without prompting"),
    (["tasks", "list"], "--include-completed"),
    (["tasks", "show"], "LIST:SERIES:TASK"),
    (["tasks", "notes"], "Manage task notes"),
    (["tasks", "notes", "add"], "--title"),
    (["tasks", "tags"], "preserving unrelated tags"),
    (["tasks", "tags", "add"], "--tag"),
    (["tasks", "tags", "remove"], "--tag"),
    (["tasks", "move"], "--to-list-id"),
]


@pytest.mark.parametrize(("arguments", "expected"), HELP_COMMANDS)
def test_command_help_is_offline(arguments, expected, capsys, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Help must not load credentials, construct a backend or prompt")

    monkeypatch.setattr("moo.cli.load_credentials", fail)
    monkeypatch.setattr("builtins.input", fail)
    with pytest.raises(SystemExit) as caught:
        run([*arguments, "--help"], fail)
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("usage: moo")
    assert expected in captured.out
    assert ("--help-all" in captured.out) == (not arguments)
    assert captured.out.endswith("\n")


def test_help_all_includes_every_command_once(capsys, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Help must not load credentials, construct a backend or prompt")

    monkeypatch.setattr("moo.cli.load_credentials", fail)
    monkeypatch.setattr("builtins.input", fail)
    with pytest.raises(SystemExit) as caught:
        run(["--help-all"], fail)
    assert caught.value.code == 0
    output = capsys.readouterr().out
    for arguments, _ in HELP_COMMANDS[1:]:
        assert output.count(f"=== moo {' '.join(arguments)} ===") == 1
    assert output.count("usage: moo") == len(HELP_COMMANDS)
    for text in [
        "--no-browser",
        "--force",
        "--list-id",
        "--include-completed",
        "--title",
        "--text",
        "--tag",
        "--to-list-id",
        "LIST:SERIES:TASK",
    ]:
        assert text in " ".join(output.split())
    assert "Agent workflow" not in output
    assert "inspect current state" not in output


@pytest.mark.parametrize("arguments", [["tasks"], ["tasks", "tags", "remove"]])
def test_help_all_is_rejected_on_subcommands(arguments, capsys):
    def fail(args):
        pytest.fail("Backend must not be constructed")

    assert run([*arguments, "--help-all"], fail) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("group", ["auth", "lists", "tasks"])
@pytest.mark.parametrize("option", ["-h", "--help"])
def test_group_help_includes_all_descendants(group, option, capsys):
    with pytest.raises(SystemExit) as caught:
        run([group, option])
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    output = captured.out
    descendants = [
        arguments
        for arguments, _ in HELP_COMMANDS
        if len(arguments) > 1 and arguments[0] == group
    ]
    for arguments in descendants:
        assert output.count(f"=== moo {' '.join(arguments)} ===") == 1
    assert output.count("usage: moo") == len(descendants) + 1
    assert "--help-all" not in output
    if group == "tasks":
        for option_name in ("--tag", "--title", "--text", "--to-list-id"):
            assert option_name in output
        assert "--no-browser" not in output


@pytest.mark.parametrize(
    "arguments", [[], ["tasks", "tags"], ["tasks", "tags", "remove"]]
)
def test_focused_help_does_not_expand_children(arguments, capsys):
    with pytest.raises(SystemExit):
        run([*arguments, "--help"])
    output = capsys.readouterr().out
    assert "--to-list-id" not in output
    assert "===" not in output


@pytest.mark.parametrize("columns", ["40", "120"])
def test_help_preserves_reference_lines(columns, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", columns)
    with pytest.raises(SystemExit):
        run(["--help-all"])
    lines = capsys.readouterr().out.splitlines()
    assert (
        'Return lists with string IDs, names, smart and archived flags as {"lists": [...]}.'
        in lines
    )
    assert (
        "usage: moo tasks list [-h] [--list-id LIST_ID] [--tag TAG] [--include-completed]"
        in lines
    )
    assert any(
        line.strip().startswith("ref")
        and line.endswith("each component containing ASCII digits")
        for line in lines
    )


def test_unicode_is_escaped(capsys):
    backend = FakeBackend()
    backend.task = replace(backend.task, name="Café\nUpdate")
    assert run(["tasks", "show", "1:2:3"], lambda args: backend) == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert len(output.splitlines()) == 1
    assert json.loads(output) == {"task": asdict(backend.task)}


def test_status(capsys):
    assert invoke(["auth", "status"], FakeBackend(), capsys) == (
        0,
        {"authorized": True},
    )


def test_no_implicit_loop(capsys):
    backend = FakeBackend()
    assert invoke(["tasks", "list"], backend, capsys)[0] == 0
    assert backend.task.tags == ["agent-ready"]
    assert backend.task.notes == []

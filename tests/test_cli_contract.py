import json
from dataclasses import asdict, replace

import pytest

from moo.cli import run
from moo.contracts import MooError, Note, Task, TaskList


class FakeBackend:
    def __init__(self):
        self.task = Task(
            "1:2:3", "1", "2", "3", "Implement feature", ["ask-ai", "unrelated"]
        )

    def lists(self):
        return [
            TaskList("1", "Agent", False, False),
            TaskList("4", "Review", False, False),
        ]

    def tasks(self, list_id, tags, include_completed):
        if list_id is not None and list_id != self.task.list_id:
            return []
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

    def command(*arguments):
        code, body = invoke(list(arguments), backend, capsys)
        assert code == 0
        return body

    discovered = command("tasks", "list", "--tag", "ask-ai")
    assert set(discovered) == {"tasks"}
    assert [task["ref"] for task in discovered["tasks"]] == ["1:2:3"]
    ref = discovered["tasks"][0]["ref"]
    before = command("tasks", "show", ref)
    assert before == {"task": discovered["tasks"][0]}

    added_note = command(
        "tasks", "notes", "add", ref, "--title", "Progress", "--text", "Tests pass"
    )
    assert added_note == {
        "ref": ref,
        "note": asdict(Note("8", "Progress", "Tests pass")),
    }
    tagged = command("tasks", "tags", "add", ref, "--tag", "pending-review")
    assert set(tagged) == {"task"}
    assert set(tagged["task"]["tags"]) == {"ask-ai", "pending-review", "unrelated"}
    untagged = command("tasks", "tags", "remove", ref, "--tag", "ask-ai")

    after = command("tasks", "show", ref)
    assert after == untagged
    assert set(after) == {"task"}
    assert after["task"]["ref"] == ref
    assert after["task"]["list_id"] == before["task"]["list_id"] == "1"
    assert after["task"]["notes"] == [added_note["note"]]
    assert set(after["task"]["tags"]) == {"pending-review", "unrelated"}
    assert command("tasks", "list", "--tag", "ask-ai") == {"tasks": []}
    assert command("tasks", "list", "--tag", "pending-review") == {
        "tasks": [after["task"]]
    }


def test_lists_and_task_list_filters(capsys):
    backend = FakeBackend()
    assert invoke(["lists", "list"], backend, capsys) == (
        0,
        {"lists": [asdict(task_list) for task_list in backend.lists()]},
    )
    assert invoke(
        ["tasks", "list", "--list-id", "1", "--tag", "ask-ai"], backend, capsys
    ) == (0, {"tasks": [asdict(backend.task)]})
    assert invoke(
        ["tasks", "list", "--list-id", "4", "--tag", "ask-ai"], backend, capsys
    ) == (0, {"tasks": []})


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["watch"],
        ["tasks", "create"],
        ["tasks", "move", "1:2:3", "--to-list-id", "4"],
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


def test_version_remains_plain_text(capsys):
    with pytest.raises(SystemExit) as caught:
        run(["--version"])
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "moo" in captured.out
    assert not captured.out.startswith("{")


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
        "LIST:SERIES:TASK",
    ]:
        assert text in " ".join(output.split())


@pytest.mark.parametrize("arguments", [["tasks"], ["tasks", "tags", "remove"]])
def test_help_all_is_rejected_on_subcommands(arguments, capsys):
    def fail(args):
        pytest.fail("Backend must not be constructed")

    assert run([*arguments, "--help-all"], fail) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("group", ["auth", "lists", "tasks"])
def test_group_help_includes_all_descendants(group, capsys):
    with pytest.raises(SystemExit) as caught:
        run([group, "--help"])
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
        for option_name in ("--tag", "--title", "--text"):
            assert option_name in output
        assert "--no-browser" not in output


@pytest.mark.parametrize(
    "arguments", [[], ["tasks", "tags"], ["tasks", "tags", "remove"]]
)
def test_focused_help_does_not_expand_children(arguments, capsys):
    with pytest.raises(SystemExit):
        run([*arguments, "--help"])
    output = capsys.readouterr().out
    assert "===" not in output


def test_short_help_matches_long_help(capsys):
    with pytest.raises(SystemExit):
        run(["tasks", "-h"])
    short_output = capsys.readouterr()
    with pytest.raises(SystemExit):
        run(["tasks", "--help"])
    assert capsys.readouterr() == short_output


@pytest.mark.parametrize("columns", ["40", "120"])
def test_help_preserves_reference_lines(columns, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", columns)
    with pytest.raises(SystemExit):
        run(["--help-all"])
    lines = capsys.readouterr().out.splitlines()
    usage = next(line for line in lines if line.startswith("usage: moo tasks list "))
    for option in ("--list-id", "--tag", "--include-completed"):
        assert option in usage
    assert any('{"lists": [...]}' in line for line in lines)
    assert any(len(line) > 120 for line in lines)


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
    assert backend.task.tags == ["ask-ai", "unrelated"]
    assert backend.task.notes == []

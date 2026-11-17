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


@pytest.mark.parametrize("code", [3, 4, 5, 6, 7])
def test_error_contract(code, capsys):
    def fail(args):
        raise MooError("failure", "Safe message", code)

    assert run(["lists", "list"], fail) == code
    body = json.loads(capsys.readouterr().out)
    assert body == {
        "error": {"code": "failure", "message": "Safe message", "details": {}},
    }


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
        "error": {"code": code, "message": message, "details": {}}
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

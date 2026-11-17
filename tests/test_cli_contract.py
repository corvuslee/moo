import json
from dataclasses import asdict

import pytest

from moo.cli import run
from moo.contracts import MooError, Note, Task, TaskList, TaskRef


class FakeBackend:
    def __init__(self):
        self.task = Task("1:2:3", "1", "2", "3", "Implement feature", ["agent-ready"])

    def lists(self):
        return [TaskList("1", "Agent", False, False), TaskList("4", "Review", False, False)]

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
    return code, json.loads(capsys.readouterr().out)


def test_agent_workflow(capsys):
    backend = FakeBackend()
    for command in [
        ["lists", "list"],
        ["tasks", "list", "--list-id", "1", "--tag", "agent-ready"],
        ["tasks", "show", "1:2:3"],
        ["tasks", "tags", "add", "1:2:3", "--tag", "in-progress"],
        ["tasks", "tags", "remove", "1:2:3", "--tag", "agent-ready"],
        ["tasks", "notes", "add", "1:2:3", "--title", "Progress", "--text", "Tests pass"],
        ["tasks", "tags", "add", "1:2:3", "--tag", "needs-review"],
        ["tasks", "move", "1:2:3", "--to-list-id", "4"],
    ]:
        code, body = invoke(command, backend, capsys)
        assert code == 0
        assert body["ok"] is True
        assert body["schema_version"] == 1
    code, body = invoke(["tasks", "list", "--tag", "agent-ready"], backend, capsys)
    assert code == 0
    assert body["data"]["tasks"] == []
    code, body = invoke(["tasks", "show", "4:2:3"], backend, capsys)
    assert body["data"]["task"]["notes"][0]["text"] == "Tests pass"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["watch"],
        ["tasks", "create"],
        ["tasks", "show", "bad"],
        ["tasks", "list", "--list-id", "not-an-id"],
        ["tasks", "list", "--tag", "two,tags"],
        ["--timeout", "nan", "lists", "list"],
        ["--timeout", "0", "lists", "list"],
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
        "schema_version": 1,
        "ok": False,
        "error": {"code": "failure", "message": "Safe message", "details": {}},
    }


def test_internal_errors_do_not_leak_secrets(capsys):
    def fail(args):
        raise RuntimeError("secret token")

    assert run(["lists", "list"], fail) == 1
    assert "secret token" not in capsys.readouterr().out


def test_status(capsys):
    assert invoke(["auth", "status"], FakeBackend(), capsys)[1]["data"]["authorized"]


def test_no_implicit_loop(capsys):
    backend = FakeBackend()
    assert invoke(["tasks", "list"], backend, capsys)[0] == 0
    assert backend.task.tags == ["agent-ready"]
    assert backend.task.notes == []

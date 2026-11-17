import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from moo.backends.rtm import RTMBackend
from moo.contracts import MooError, TaskRef


def test_auth_requests_delete_permission_for_list_moves():
    backend = RTMBackend("key", "secret", None)
    backend.call = Mock(return_value={"frob": "temporary"})
    query = parse_qs(urlsplit(backend.auth_url()).query)
    assert query["perms"] == ["delete"]
    assert query["frob"] == ["temporary"]
    assert "auth_token" not in query


@pytest.mark.parametrize("write_attempted", [False, True])
def test_wall_clock_deadline_interrupts_blocking_operation(write_attempted):
    backend = RTMBackend("key", "secret", "token")
    backend._timeout = 0.02
    started = time.monotonic()

    def operation():
        backend.write_attempted = write_attempted
        time.sleep(10)

    with pytest.raises(MooError) as error:
        backend.run(operation)
    assert error.value.exit_code == 6
    assert error.value.code == "deadline_exceeded"
    assert error.value.message == (
        "Could not confirm the operation. It may have succeeded. "
        "Check the task's current state before retrying."
        if write_attempted
        else "Operation deadline exceeded; no retry performed."
    )
    assert error.value.details is None
    assert backend.write_attempted is write_attempted
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("remove", [True, False])
def test_tag_mutations_use_additive_endpoints_and_owned_output(remove):
    backend = RTMBackend("key", "secret", "token")
    prepared = {
        "timeline": "1",
        "list_id": "1",
        "taskseries_id": "2",
        "task_id": "3",
    }
    backend._prepare = Mock(return_value=prepared)
    backend.call = Mock(
        return_value={
            "list": {
                "id": "1",
                "taskseries": {
                    "id": "2",
                    "name": "Task",
                    "tags": {"tag": ["other"]},
                    "task": {"id": "3"},
                },
            }
        }
    )
    result = backend.change_tags(
        TaskRef("1", "2", "3"), ["agent-ready", "review"], remove
    )
    task = result["task"]
    assert isinstance(task, dict)
    assert task["tags"] == ["other"]
    backend.call.assert_called_once_with(
        "tasks.removeTags" if remove else "tasks.addTags",
        {**prepared, "tags": "agent-ready,review"},
        mutation=True,
    )


def test_requests_use_remaining_operation_budget(monkeypatch):
    backend = RTMBackend("key", "secret", "token")
    response = Mock()
    response.read.return_value = b'{"rsp":{"stat":"ok"}}'
    backend.opener = Mock()
    backend.opener.open.return_value.__enter__ = Mock(return_value=response)
    backend.opener.open.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        "moo.backends.rtm.signal.getitimer",
        Mock(side_effect=[(8.0, 0), (3.0, 0)]),
    )
    backend.call("lists.getList")
    backend.call("tasks.getList")
    assert [call.kwargs["timeout"] for call in backend.opener.open.call_args_list] == [
        8.0,
        3.0,
    ]


def test_nested_deadline_preserves_outer_budget(monkeypatch):
    from moo.backends import rtm

    previous_handler = Mock()
    install_handler = Mock(return_value=previous_handler)
    set_timer = Mock()
    monkeypatch.setattr(rtm.signal, "signal", install_handler)
    monkeypatch.setattr(rtm.signal, "getitimer", Mock(return_value=(4.0, 0)))
    monkeypatch.setattr(rtm.signal, "setitimer", set_timer)
    monkeypatch.setattr(rtm, "monotonic", Mock(side_effect=[10.0, 11.0]))
    with rtm.deadline(10.0):
        assert set_timer.call_args.args == (rtm.signal.ITIMER_REAL, 4.0)
    assert [call.args for call in set_timer.call_args_list] == [
        (rtm.signal.ITIMER_REAL, 4.0),
        (rtm.signal.ITIMER_REAL, 0),
        (rtm.signal.ITIMER_REAL, 3.0, 0),
    ]
    assert install_handler.call_args.args == (rtm.signal.SIGALRM, previous_handler)

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


@pytest.mark.parametrize("code", [320, 340])
def test_missing_remote_identifiers_are_not_found(code):
    backend = RTMBackend("key", "secret", "token")
    response = Mock()
    response.read.return_value = (
        '{"rsp":{"stat":"fail","err":{"code":"' + str(code) + '"}}}'
    ).encode()
    backend.opener.open = Mock()
    backend.opener.open.return_value.__enter__ = Mock(return_value=response)
    backend.opener.open.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(MooError) as error:
        backend.call("tasks.getList")
    assert error.value.exit_code == 4


def test_wall_clock_deadline_interrupts_blocking_operation():
    backend = RTMBackend("key", "secret", "token", timeout=0.02)
    started = time.monotonic()
    with pytest.raises(MooError) as error:
        backend.run(lambda: time.sleep(10))
    assert error.value.exit_code == 6
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("remove", [True, False])
def test_tag_mutations_use_additive_endpoints_and_owned_output(remove):
    backend = RTMBackend("key", "secret", "token")
    backend._prepare = Mock(return_value={"timeline": "1"})
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
    result = backend.change_tags(TaskRef("1", "2", "3"), ["agent-ready"], remove)
    task = result["task"]
    assert isinstance(task, dict)
    assert task["tags"] == ["other"]
    backend.call.assert_called_once_with(
        "tasks.removeTags" if remove else "tasks.addTags",
        {"timeline": "1", "tags": "agent-ready"},
        mutation=True,
    )

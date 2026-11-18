import json
from contextlib import nullcontext
from email.message import Message
from hashlib import md5
from http.client import HTTPException, IncompleteRead, RemoteDisconnected
from unittest.mock import MagicMock, Mock, call
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request

import pytest

from moo.backends import rtm
from moo.contracts import MooError, Note, TaskRef

UNCERTAIN_WRITE_MESSAGE = (
    "Could not confirm the operation. It may have succeeded. "
    "Check the task's current state before retrying."
)


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(rtm, "deadline", lambda seconds: nullcontext())
    value = rtm.RTMBackend("key", "secret", "token")
    value.opener = MagicMock()
    return value


def respond(backend, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    response = Mock()
    response.read.return_value = raw
    backend.opener.open.return_value.__enter__.return_value = response
    return response


def task_response(*, recurring=False, completed="", tags=None, notes=None):
    series = {
        "id": "20",
        "name": "Buy milk",
        "tags": tags or {},
        "notes": notes or {},
        "task": {"id": "30", "completed": completed, "due": "", "priority": "N"},
    }
    if recurring:
        series["rrule"] = {"$t": "FREQ=DAILY"}
    return {"tasks": {"list": {"id": "10", "taskseries": series}}}


def assert_error(error, code, exit_code):
    assert error.value.code == code
    assert error.value.exit_code == exit_code


def test_signature_uses_sorted_unescaped_utf8_parameters():
    params = {"z": "a & b", "a": "café", "method": "rtm.tasks.getList"}
    expected = md5(
        "secretacafémethodrtm.tasks.getListza & b".encode(),
        usedforsecurity=False,
    ).hexdigest()
    assert rtm.signature("secret", params) == expected
    assert rtm.signature("secret", dict(reversed(list(params.items())))) == expected
    assert params == {"z": "a & b", "a": "café", "method": "rtm.tasks.getList"}


def test_call_signs_post_and_passes_timeout(backend):
    response = respond(backend, {"rsp": {"stat": "ok", "value": 42}})
    assert backend.call("tasks.getList", {"filter": "tag:café & milk"})["value"] == 42
    request = backend.opener.open.call_args.args[0]
    assert request.full_url == rtm.REST_URL
    assert request.get_method() == "POST"
    params = {key: values[0] for key, values in parse_qs(request.data.decode()).items()}
    signature = params.pop("api_sig")
    assert params == {
        "method": "rtm.tasks.getList",
        "api_key": "key",
        "format": "json",
        "auth_token": "token",
        "filter": "tag:café & milk",
    }
    assert signature == rtm.signature("secret", params)
    backend.opener.open.assert_called_once_with(request, timeout=10.0)
    response.read.assert_called_once_with(8_000_001)


def test_missing_token_fails_without_transport(backend):
    backend.token = None
    with pytest.raises(MooError) as error:
        backend.call("tasks.getList")
    assert_error(error, "authentication_required", 3)
    backend.opener.open.assert_not_called()


def test_unauthenticated_call_omits_token(backend):
    backend.token = None
    respond(backend, {"rsp": {"stat": "ok", "frob": "abc"}})
    backend.call("auth.getFrob", authenticated=False)
    params = parse_qs(backend.opener.open.call_args.args[0].data.decode())
    assert "auth_token" not in params


@pytest.mark.parametrize("empty", [None, "", [], {}])
def test_empty_task_collections(empty):
    assert rtm.normalize_tasks({"tasks": {"list": empty}}) == []
    assert (
        rtm.normalize_tasks({"tasks": {"list": {"id": "10", "taskseries": empty}}})
        == []
    )


@pytest.mark.parametrize("plural", [False, True])
def test_single_and_plural_task_shapes(plural):
    body = task_response()
    task_list = body["tasks"]["list"]
    series = task_list["taskseries"]
    if plural:
        series["task"] = [series["task"], {"id": "31", "completed": "2026-01-01"}]
        task_list["taskseries"] = [series]
        body["tasks"]["list"] = [task_list]
    tasks = rtm.normalize_tasks(body)
    assert [task.ref for task in tasks] == (
        ["10:20:30", "10:20:31"] if plural else ["10:20:30"]
    )
    assert tasks[0].name == "Buy milk"
    assert tasks[0].completed is None
    assert tasks[0].due is None
    assert tasks[0].priority == "N"
    assert tasks[0].recurring is False
    if plural:
        assert tasks[1].completed == "2026-01-01"


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({}, []),
        ({"tag": []}, []),
        ({"tag": "milk"}, ["milk"]),
        ({"tag": ["z", "a"]}, ["a", "z"]),
        (["z", "a"], ["a", "z"]),
    ],
)
def test_tag_shapes(tags, expected):
    assert rtm.normalize_tasks(task_response(tags=tags))[0].tags == expected


@pytest.mark.parametrize("plural", [False, True])
def test_note_shapes(plural):
    note = {"id": 7, "title": "Title", "$t": "Text", "created": "", "modified": "date"}
    notes = {"note": [note, {"id": "8"}]} if plural else {"note": note}
    values = rtm.normalize_tasks(task_response(notes=notes))[0].notes
    assert values[0] == Note("7", "Title", "Text", None, "date")
    assert values[1:] == ([Note("8", "", "")] if plural else [])
    assert rtm.normalize_tasks(task_response())[0].notes == []


def test_tasks_requires_all_requested_tags(backend):
    body = task_response(tags={"tag": ["milk", "shop", "extra"]})
    matching = body["tasks"]["list"]["taskseries"]
    body["tasks"]["list"]["taskseries"] = [
        matching,
        {**matching, "id": "21", "tags": {"tag": "milk"}},
        {**matching, "id": "22", "tags": {}},
    ]
    backend.call = Mock(return_value=body)
    assert [task.ref for task in backend.tasks(None, ["milk", "shop"], False)] == [
        "10:20:30"
    ]
    backend.call.assert_called_once_with(
        "tasks.getList", {"filter": "status:incomplete"}
    )


@pytest.mark.parametrize(
    "include_completed,expected_filter", [(False, "status:incomplete"), (True, "")]
)
def test_tasks_sends_completion_filter(backend, include_completed, expected_filter):
    backend.call = Mock(return_value=task_response())
    assert [task.ref for task in backend.tasks(None, [], include_completed)] == [
        "10:20:30"
    ]
    backend.call.assert_called_once_with("tasks.getList", {"filter": expected_filter})


def test_tasks_validates_and_selects_list(backend):
    backend.call = Mock(
        side_effect=[
            {"lists": {"list": [{"id": "10", "name": "Source"}]}},
            task_response(),
        ]
    )
    assert [task.ref for task in backend.tasks("10", [], False)] == ["10:20:30"]
    assert backend.call.call_args_list == [
        call("lists.getList"),
        call("tasks.getList", {"filter": "status:incomplete", "list_id": "10"}),
    ]


@pytest.mark.parametrize("found", [False, True])
def test_show_requires_exact_reference(backend, found):
    body = task_response()
    series = body["tasks"]["list"]["taskseries"]
    series["task"] = [{"id": "31"}]
    body["tasks"]["list"]["taskseries"] = [
        {**series, "id": "21", "task": {"id": "30"}},
        series,
    ]
    if found:
        series["task"].append({"id": "30"})
    backend.call = Mock(return_value=body)
    ref = TaskRef("10", "20", "30")
    if found:
        assert backend.show(ref).ref == str(ref)
    else:
        with pytest.raises(MooError) as error:
            backend.show(ref)
        assert_error(error, "not_found", 4)
        assert error.value.message == (
            "Task reference not found; rediscover after moving a task."
        )
    backend.call.assert_called_once_with(
        "tasks.getList", {"list_id": "10", "filter": ""}
    )


def test_deleted_tasks_are_omitted_and_recurring_is_preserved():
    body = task_response(recurring=True)
    series = body["tasks"]["list"]["taskseries"]
    series["task"] = [series["task"], {"id": "31", "deleted": "2026-01-01"}]
    tasks = rtm.normalize_tasks(body)
    assert len(tasks) == 1
    assert tasks[0].recurring is True


@pytest.mark.parametrize("mutation", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        URLError("offline"),
        OSError("closed"),
        TimeoutError(),
        HTTPException("protocol failure"),
        RemoteDisconnected("closed"),
    ],
)
def test_transport_failure_is_not_retried_and_reports_uncertainty(
    backend, mutation, failure
):
    backend.opener.open.side_effect = failure
    with pytest.raises(MooError) as error:
        backend.call("tasks.addTags", mutation=mutation)
    assert_error(error, "transport_error", 6)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE
        if mutation
        else "RTM request failed or timed out; no retry performed."
    )
    assert error.value.details is None
    assert backend.write_attempted is mutation
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize("mutation", [False, True])
def test_incomplete_response_is_not_retried_and_reports_uncertainty(backend, mutation):
    response = respond(backend, b"")
    response.read.side_effect = IncompleteRead(b"partial", 100)
    with pytest.raises(MooError) as error:
        backend.run(lambda: backend.call("tasks.addTags", mutation=mutation))
    assert_error(error, "transport_error", 6)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE
        if mutation
        else "RTM request failed or timed out; no retry performed."
    )
    assert error.value.details is None
    assert backend.write_attempted is mutation
    backend.opener.open.assert_called_once()
    response.read.assert_called_once_with(8_000_001)


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "rate_limited"),
        (500, "upstream_error"),
        (301, "upstream_error"),
        (302, "upstream_error"),
    ],
)
@pytest.mark.parametrize("mutation", [False, True])
def test_http_error_mapping_without_retries(backend, status, code, mutation):
    backend.opener.open.side_effect = HTTPError(
        rtm.REST_URL, status, "failed", Message(), None
    )
    with pytest.raises(MooError) as error:
        backend.call(
            "tasks.addTags" if mutation else "tasks.getList", mutation=mutation
        )
    assert_error(error, code, 7)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE if mutation else "RTM HTTP request failed."
    )
    assert error.value.details == {"http_status": status}
    assert backend.write_attempted is mutation
    backend.opener.open.assert_called_once()


def test_redirect_handler_blocks_following_redirects(monkeypatch):
    opener = Mock()
    factory = Mock(return_value=opener)
    monkeypatch.setattr(rtm, "build_opener", factory)
    backend = rtm.RTMBackend("key", "secret", "token")
    handler = factory.call_args.args[0]
    assert isinstance(handler, rtm.NoRedirect)
    assert backend.opener is opener
    assert (
        handler.redirect_request(
            Request(rtm.REST_URL), None, 302, "Found", {}, "https://attacker.invalid/"
        )
        is None
    )


@pytest.mark.parametrize(
    "raw,mutation",
    [
        (b"not json", False),
        (b"\xff", True),
        (b"[]", False),
        (b"null", True),
        (b"{}", False),
        (b'{"rsp": null}', True),
        (b'{"rsp": []}', False),
        (b'{"rsp": {}}', True),
        (b'{"rsp": {"stat": "unknown"}}', False),
        (b'{"rsp": {"stat": []}}', True),
    ],
)
def test_malformed_envelopes_are_structured_errors(backend, raw, mutation):
    respond(backend, raw)
    with pytest.raises(MooError) as error:
        backend.call("tasks.addTags", mutation=mutation)
    assert_error(error, "invalid_response", 7)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE if mutation else "RTM returned an invalid response."
    )
    assert error.value.details is None
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize("mutation", [False, True])
def test_oversized_response_is_rejected(backend, mutation):
    respond(backend, b" " * 8_000_001)
    with pytest.raises(MooError) as error:
        backend.call("tasks.getList", mutation=mutation)
    assert_error(error, "response_too_large", 7)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE if mutation else "RTM response exceeded 8 MB."
    )
    assert error.value.details is None
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize(
    "err,mutation", [(None, False), ([], True), ("bad", False), (5, True)]
)
def test_malformed_api_errors_are_structured_errors(backend, err, mutation):
    respond(backend, {"rsp": {"stat": "fail", "err": err}})
    with pytest.raises(MooError) as error:
        backend.call("tasks.addTags", mutation=mutation)
    assert_error(error, "invalid_response", 7)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE if mutation else "RTM returned an invalid error."
    )
    assert error.value.details is None
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize(
    "api_code,code,exit_code",
    [
        (96, "authentication_failed", 3),
        (104, "authentication_failed", 3),
        (105, "upstream_error", 7),
        (320, "not_found", 4),
        (340, "not_found", 4),
        (1, "operation_rejected", 5),
        (999, "operation_rejected", 5),
    ],
)
def test_api_error_mappings(backend, api_code, code, exit_code):
    respond(backend, {"rsp": {"stat": "fail", "err": {"code": api_code, "msg": "no"}}})
    with pytest.raises(MooError) as error:
        backend.call("tasks.getList")
    assert_error(error, code, exit_code)
    assert error.value.details == {"api_code": str(api_code)}
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        {"tasks": None},
        {"tasks": []},
        {"tasks": {"list": "bad"}},
        {"tasks": {"list": {"id": "10", "taskseries": 3}}},
        task_response(tags={"tag": [3]}),
        task_response(notes={"note": [{}]}),
    ],
)
def test_malformed_task_shapes_are_structured_errors(backend, body):
    respond(backend, {"rsp": {"stat": "ok", **body}})
    with pytest.raises(MooError) as error:
        backend.tasks(None, [], False)
    assert_error(error, "invalid_response", 7)
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize("write_attempted", [False, True])
def test_operation_deadline_reports_prior_write_and_does_not_retry(
    backend, write_attempted
):
    def operation():
        backend.write_attempted = write_attempted
        raise TimeoutError

    operation_mock = Mock(side_effect=operation)
    with pytest.raises(MooError) as error:
        backend.run(operation_mock)
    assert_error(error, "deadline_exceeded", 6)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE
        if write_attempted
        else "Operation deadline exceeded; no retry performed."
    )
    assert error.value.details is None
    assert backend.write_attempted is write_attempted
    operation_mock.assert_called_once_with()


@pytest.mark.parametrize(
    "failure,write_attempted",
    [(KeyError, False), (TypeError, True), (AttributeError, False), (ValueError, True)],
)
def test_operation_shape_failure_reports_prior_write(backend, write_attempted, failure):
    def operation():
        backend.write_attempted = write_attempted
        raise failure

    operation_mock = Mock(side_effect=operation)
    with pytest.raises(MooError) as error:
        backend.run(operation_mock)
    assert_error(error, "invalid_response", 7)
    assert error.value.message == (
        UNCERTAIN_WRITE_MESSAGE
        if write_attempted
        else "RTM returned an unexpected response shape."
    )
    assert error.value.details is None
    assert backend.write_attempted is write_attempted
    operation_mock.assert_called_once_with()


@pytest.mark.parametrize("mutation", ["note", "add_tags", "remove_tags"])
def test_missing_mutation_result_reports_uncertain_write(backend, mutation):
    backend._prepare = Mock(
        return_value={
            "timeline": "timeline",
            "list_id": "10",
            "taskseries_id": "20",
            "task_id": "30",
        }
    )
    respond(backend, {"rsp": {"stat": "ok"}})
    ref = TaskRef("10", "20", "30")
    operations = {
        "note": lambda: backend.add_note(ref, "Title", "Text"),
        "add_tags": lambda: backend.change_tags(ref, ["milk"], False),
        "remove_tags": lambda: backend.change_tags(ref, ["milk"], True),
    }
    with pytest.raises(MooError) as error:
        operations[mutation]()
    assert_error(error, "invalid_response", 7)
    assert error.value.message == UNCERTAIN_WRITE_MESSAGE
    assert error.value.details is None
    assert backend.write_attempted is True
    backend.opener.open.assert_called_once()


@pytest.mark.parametrize(
    "mutation,state,code,exit_code",
    [
        ("note", "recurring", "invalid_state", 5),
        ("add_tags", "completed", "invalid_state", 5),
        ("remove_tags", "smart", "invalid_state", 5),
        ("note", "archived", "invalid_state", 5),
        ("add_tags", "missing_list", "not_found", 4),
        ("remove_tags", "missing_task", "not_found", 4),
    ],
)
def test_unsafe_mutations_are_rejected_before_timeline_or_write(
    backend, mutation, state, code, exit_code
):
    lists = [
        {
            "id": "11" if state == "missing_list" else "10",
            "name": "Source",
            "smart": "1" if state == "smart" else "0",
            "archived": "1" if state == "archived" else "0",
        },
    ]
    body = task_response(
        recurring=state == "recurring", completed="date" if state == "completed" else ""
    )
    if state == "missing_task":
        body["tasks"]["list"]["taskseries"]["task"]["id"] = "31"

    def call(method, params=None, **kwargs):
        assert not kwargs.get("mutation", False)
        if method == "lists.getList":
            return {"lists": {"list": lists}}
        assert method == "tasks.getList"
        return body

    backend.call = Mock(side_effect=call)
    ref = TaskRef("10", "20", "30")
    operations = {
        "note": lambda: backend.add_note(ref, "Title", "Text"),
        "add_tags": lambda: backend.change_tags(ref, ["milk"], False),
        "remove_tags": lambda: backend.change_tags(ref, ["milk"], True),
    }
    with pytest.raises(MooError) as error:
        operations[mutation]()
    assert_error(error, code, exit_code)
    expected_calls = ["lists.getList"]
    if state in {"recurring", "completed", "missing_task"}:
        expected_calls.append("tasks.getList")
    assert [call.args[0] for call in backend.call.call_args_list] == expected_calls
    assert backend.write_attempted is False
    backend.opener.open.assert_not_called()


def test_add_note_returns_owned_normalized_note(backend):
    upstream = {
        "id": 7,
        "title": "Title",
        "$t": "Text",
        "created": "",
        "modified": "date",
        "extra": "ignored",
    }
    ref = TaskRef("10", "20", "30")
    backend._prepare = Mock(
        return_value={
            "timeline": "timeline",
            "list_id": "10",
            "taskseries_id": "20",
            "task_id": "30",
        }
    )
    backend.call = Mock(return_value={"note": upstream})
    assert backend.add_note(ref, "Title", "Text") == {
        "ref": "10:20:30",
        "note": {
            "id": "7",
            "title": "Title",
            "text": "Text",
            "created": None,
            "modified": "date",
        },
    }
    backend._prepare.assert_called_once_with(ref)
    backend.call.assert_called_once_with(
        "tasks.notes.add",
        {
            "timeline": "timeline",
            "list_id": "10",
            "taskseries_id": "20",
            "task_id": "30",
            "note_title": "Title",
            "note_text": "Text",
        },
        mutation=True,
    )


def test_tag_mutation_returning_a_different_task_reports_uncertain_write(backend):
    prepared = {
        "timeline": "timeline",
        "list_id": "10",
        "taskseries_id": "20",
        "task_id": "30",
    }
    backend._prepare = Mock(return_value=prepared)
    task_list = task_response()["tasks"]["list"]
    task_list["taskseries"]["task"]["id"] = "31"
    respond(backend, {"rsp": {"stat": "ok", "list": task_list}})
    ref = TaskRef("10", "20", "30")
    with pytest.raises(MooError) as error:
        backend.change_tags(ref, ["milk"], True)
    assert_error(error, "invalid_response", 7)
    assert error.value.message == UNCERTAIN_WRITE_MESSAGE
    assert error.value.details is None
    assert backend.write_attempted is True
    backend._prepare.assert_called_once_with(ref)
    backend.opener.open.assert_called_once()
    params = parse_qs(backend.opener.open.call_args.args[0].data.decode())
    assert params["method"] == ["rtm.tasks.removeTags"]
    assert params["tags"] == ["milk"]
    for key, value in prepared.items():
        assert params[key] == [value]

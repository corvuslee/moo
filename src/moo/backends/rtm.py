from __future__ import annotations

import json
import signal
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import md5
from http.client import HTTPException
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from moo.contracts import MooError, Note, Task, TaskList, TaskRef

REST_URL = "https://api.rememberthemilk.com/services/rest/"
AUTH_URL = "https://www.rememberthemilk.com/services/auth/"
OPERATION_TIMEOUT = 10.0


def signature(secret: str, params: dict[str, str]) -> str:
    payload = secret + "".join(key + params[key] for key in sorted(params))
    return md5(payload.encode(), usedforsecurity=False).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def deadline(seconds: float):
    def expired(signum, frame):
        raise TimeoutError

    if seconds <= 0:
        raise TimeoutError
    previous = signal.signal(signal.SIGALRM, expired)
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    started = monotonic()
    signal.setitimer(
        signal.ITIMER_REAL, min(seconds, remaining) if remaining else seconds
    )
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if remaining:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(remaining - (monotonic() - started), 1e-6),
                interval,
            )


def items(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise MooError("invalid_response", "RTM returned an unexpected collection.", 7)


def normalize_tasks(response: dict[str, Any]) -> list[Task]:
    result = []
    for task_list in items(response.get("tasks", {}).get("list")):
        for series in items(task_list.get("taskseries")):
            raw_tags = series.get("tags") or {}
            tags = raw_tags.get("tag", []) if isinstance(raw_tags, dict) else raw_tags
            if isinstance(tags, str):
                tags = [tags]
            if not isinstance(tags, list) or not all(
                isinstance(tag, str) for tag in tags
            ):
                raise MooError("invalid_response", "RTM returned invalid tags.", 7)
            raw_notes = series.get("notes") or {}
            notes = [
                Note(
                    str(note["id"]),
                    note.get("title", ""),
                    note.get("$t", ""),
                    note.get("created") or None,
                    note.get("modified") or None,
                )
                for note in items(
                    raw_notes.get("note") if isinstance(raw_notes, dict) else raw_notes
                )
            ]
            for task in items(series.get("task")):
                if task.get("deleted"):
                    continue
                ref = TaskRef(str(task_list["id"]), str(series["id"]), str(task["id"]))
                result.append(
                    Task(
                        str(ref),
                        ref.list_id,
                        ref.series_id,
                        ref.task_id,
                        series["name"],
                        sorted(tags),
                        notes,
                        task.get("completed") or None,
                        task.get("due") or None,
                        task.get("priority") or None,
                        bool(series.get("rrule")),
                    )
                )
    return result


class RTMBackend:
    def __init__(self, api_key: str, shared_secret: str, token: str | None):
        self.api_key = api_key
        self.shared_secret = shared_secret
        self.token = token
        self._timeout = OPERATION_TIMEOUT
        self.frob: str | None = None
        self.opener = build_opener(NoRedirect())
        self.write_attempted = False

    def failure(
        self,
        code: str,
        message: str,
        exit_code: int,
        details: dict[str, object] | None = None,
    ) -> MooError:
        if self.write_attempted:
            message = (
                "Could not confirm the operation. It may have succeeded. "
                "Check the task's current state before retrying."
            )
        return MooError(code, message, exit_code, details)

    def call(
        self,
        method: str,
        params: dict[str, str] | None = None,
        authenticated: bool = True,
        mutation: bool = False,
    ) -> dict[str, Any]:
        payload = {"method": "rtm." + method, "api_key": self.api_key, "format": "json"}
        payload.update(params or {})
        if authenticated:
            if not self.token:
                raise MooError(
                    "authentication_required", "Run moo auth to authorize access.", 3
                )
            payload["auth_token"] = self.token
        payload["api_sig"] = signature(self.shared_secret, payload)
        request = Request(REST_URL, data=urlencode(payload).encode(), method="POST")
        self.write_attempted = self.write_attempted or mutation
        try:
            remaining = signal.getitimer(signal.ITIMER_REAL)[0]
            timeout = min(self._timeout, remaining) if remaining else self._timeout
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise self.failure(
                    "response_too_large", "RTM response exceeded 8 MB.", 7
                )
            body = json.loads(raw)["rsp"]
            if not isinstance(body, dict) or body.get("stat") not in {"ok", "fail"}:
                raise ValueError
        except HTTPError as error:
            code = "rate_limited" if error.code == 429 else "upstream_error"
            raise self.failure(
                code, "RTM HTTP request failed.", 7, {"http_status": error.code}
            ) from None
        except (HTTPException, URLError, OSError, TimeoutError):
            raise self.failure(
                "transport_error",
                "RTM request failed or timed out; no retry performed.",
                6,
            ) from None
        except (ValueError, KeyError, TypeError):
            raise self.failure(
                "invalid_response",
                "RTM returned an invalid response.",
                7,
            ) from None
        if body["stat"] == "fail":
            error_data = body.get("err")
            if not isinstance(error_data, dict):
                raise self.failure(
                    "invalid_response",
                    "RTM returned an invalid error.",
                    7,
                )
            api_code = str(error_data.get("code", ""))
            if api_code == "105":
                raise MooError(
                    "upstream_error",
                    "RTM is temporarily unavailable.",
                    7,
                    {"api_code": api_code},
                )
            if api_code in {"320", "340"}:
                raise MooError(
                    "not_found",
                    "RTM list or task reference not found.",
                    4,
                    {"api_code": api_code},
                )
            if api_code in {"96", "97", "98", "99", "100", "101", "102", "103", "104"}:
                raise MooError(
                    "authentication_failed",
                    "Check app credentials or run moo auth --force.",
                    3,
                    {"api_code": api_code},
                )
            raise MooError(
                "operation_rejected",
                "RTM rejected the operation.",
                5,
                {"api_code": api_code},
            )
        return body

    def run(self, operation):
        self.write_attempted = False
        try:
            with deadline(self._timeout):
                return operation()
        except MooError as error:
            if error.code == "invalid_response" and self.write_attempted:
                raise self.failure(
                    error.code, error.message, error.exit_code, error.details
                ) from None
            raise
        except TimeoutError:
            raise self.failure(
                "deadline_exceeded",
                "Operation deadline exceeded; no retry performed.",
                6,
            ) from None
        except (KeyError, TypeError, AttributeError, ValueError):
            raise self.failure(
                "invalid_response",
                "RTM returned an unexpected response shape.",
                7,
            ) from None

    def auth_url(self) -> str:
        self.frob = str(
            self.run(lambda: self.call("auth.getFrob", authenticated=False))["frob"]
        )
        params = {"api_key": self.api_key, "perms": "delete", "frob": self.frob}
        params["api_sig"] = signature(self.shared_secret, params)
        return AUTH_URL + "?" + urlencode(params)

    def exchange_token(self) -> str:
        if not self.frob:
            raise MooError(
                "authentication_failed", "No pending authorization. Run moo auth.", 3
            )
        response = self.run(
            lambda: self.call(
                "auth.getToken", {"frob": self.frob or ""}, authenticated=False
            )
        )
        return str(response["auth"]["token"])

    def check_token(self) -> dict[str, object]:
        def check():
            auth = self.call("auth.checkToken")["auth"]
            user = auth.get("user", {})
            return {
                "authorized": True,
                "permissions": auth.get("perms"),
                "username": user.get("username"),
                "user_id": user.get("id"),
            }

        return self.run(check)

    def _lists(self) -> list[TaskList]:
        return [
            TaskList(
                str(value["id"]),
                value["name"],
                str(value.get("smart", "0")) == "1",
                str(value.get("archived", "0")) == "1",
            )
            for value in items(self.call("lists.getList").get("lists", {}).get("list"))
        ]

    def lists(self) -> list[TaskList]:
        return self.run(self._lists)

    def _list(self, list_id: str, writable: bool = False) -> TaskList:
        found = next((value for value in self._lists() if value.id == list_id), None)
        if found is None:
            raise MooError("not_found", "List not found.", 4)
        if writable and (found.smart or found.archived):
            raise MooError(
                "invalid_state", "Mutations require a non-archived regular list.", 5
            )
        return found

    def tasks(
        self, list_id: str | None, tags: list[str], include_completed: bool
    ) -> list[Task]:
        def query():
            params = {"filter": "" if include_completed else "status:incomplete"}
            if list_id:
                self._list(list_id)
                params["list_id"] = list_id
            values = normalize_tasks(self.call("tasks.getList", params))
            return [value for value in values if set(tags).issubset(value.tags)]

        return self.run(query)

    def _show(self, ref: TaskRef) -> Task:
        values = normalize_tasks(
            self.call("tasks.getList", {"list_id": ref.list_id, "filter": ""})
        )
        for value in values:
            if value.ref == str(ref):
                return value
        raise MooError(
            "not_found", "Task reference not found; rediscover after moving a task.", 4
        )

    def show(self, ref: TaskRef) -> Task:
        return self.run(lambda: self._show(ref))

    def _prepare(self, ref: TaskRef) -> dict[str, str]:
        self._list(ref.list_id, writable=True)
        task = self._show(ref)
        if task.recurring or task.completed:
            raise MooError(
                "invalid_state",
                "Mutations of recurring or completed tasks are unsupported.",
                5,
            )
        timeline = str(self.call("timelines.create")["timeline"])
        return {
            "timeline": timeline,
            "list_id": ref.list_id,
            "taskseries_id": ref.series_id,
            "task_id": ref.task_id,
        }

    def add_note(self, ref: TaskRef, title: str, text: str) -> dict[str, object]:
        def mutate():
            params = self._prepare(ref)
            response = self.call(
                "tasks.notes.add",
                {**params, "note_title": title, "note_text": text},
                mutation=True,
            )
            note = response["note"]
            normalized = Note(
                str(note["id"]),
                note.get("title", ""),
                note.get("$t", ""),
                note.get("created") or None,
                note.get("modified") or None,
            )
            return {"ref": str(ref), "note": asdict(normalized)}

        return self.run(mutate)

    def change_tags(
        self, ref: TaskRef, tags: list[str], remove: bool
    ) -> dict[str, object]:
        def mutate():
            params = self._prepare(ref)
            method = "tasks.removeTags" if remove else "tasks.addTags"
            response = self.call(
                method, {**params, "tags": ",".join(tags)}, mutation=True
            )
            tasks = normalize_tasks({"tasks": {"list": response["list"]}})
            task = next((task for task in tasks if task.task_id == ref.task_id), None)
            if task is None:
                raise ValueError
            return {"task": asdict(task)}

        return self.run(mutate)

    def move(self, ref: TaskRef, destination: str) -> dict[str, object]:
        def mutate():
            self._list(destination, writable=True)
            params = self._prepare(ref)
            params.pop("list_id")
            response = self.call(
                "tasks.moveTo",
                {**params, "from_list_id": ref.list_id, "to_list_id": destination},
                mutation=True,
            )
            moved = normalize_tasks({"tasks": {"list": response["list"]}})
            if len(moved) != 1 or moved[0].list_id != destination:
                raise ValueError
            return {"ref": moved[0].ref, "previous_ref": str(ref)}

        return self.run(mutate)

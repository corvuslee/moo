import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol, TypeVar

from moo.backends.rtm import OPERATION_TIMEOUT, deadline
from moo.contracts import MooError

T = TypeVar("T")


@dataclass(frozen=True)
class Credentials:
    api_key: str
    shared_secret: str
    token: str | None = None


class AuthBackend(Protocol):
    def auth_url(self) -> str: ...
    def exchange_token(self) -> str: ...
    def check_token(self) -> dict[str, object]: ...


def _config_path() -> Path:
    override = os.environ.get("MOO_CONFIG")
    if override:
        return Path(override).expanduser()
    root = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(root).expanduser() if root else Path.home() / ".config"
    ) / "moo/credentials.json"


def _read_config() -> dict[str, object]:
    try:
        with _config_path().open(encoding="utf-8") as stream:
            data: object = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise MooError(
            "config_error", "Cannot read the credentials configuration.", 3
        ) from None
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise MooError(
            "config_error", "Credentials configuration must be a JSON object.", 3
        )
    result: dict[str, object] = dict(data)
    for key in ("api_key", "shared_secret", "token"):
        value = result.get(key)
        if value is not None and not isinstance(value, str):
            raise MooError(
                "config_error", "Credentials configuration has invalid fields.", 3
            )
    return result


def load_credentials(require_token: bool = True) -> Credentials:
    saved = _read_config()
    api_key = os.environ.get("RTM_API_KEY", saved.get("api_key"))
    secret = os.environ.get("RTM_SHARED_SECRET", saved.get("shared_secret"))
    token = os.environ.get("RTM_AUTH_TOKEN")
    if (
        token is None
        and api_key == saved.get("api_key")
        and secret == saved.get("shared_secret")
    ):
        stored_token = saved.get("token")
        token = stored_token if isinstance(stored_token, str) else None
    if (
        not isinstance(api_key, str)
        or not api_key
        or not isinstance(secret, str)
        or not secret
    ):
        raise MooError(
            "config_error",
            "Set RTM_API_KEY and RTM_SHARED_SECRET or save app credentials.",
            3,
        )
    if require_token and not token:
        raise MooError(
            "config_error", "No authentication token configured. Run moo auth.", 3
        )
    return Credentials(api_key, secret, token or None)


def _write_config(data: dict[str, object]) -> None:
    temporary: str | None = None
    try:
        path = _config_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".credentials-",
            delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError):
        raise MooError(
            "config_error", "Cannot save the credentials configuration.", 3
        ) from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def save_credentials(credentials: Credentials) -> None:
    _write_config(asdict(credentials))


def logout() -> dict[str, object]:
    saved = _read_config()
    removed = bool(saved.pop("token", None))
    if saved or removed:
        _write_config(saved)
    return {
        "logged_out": True,
        "token_removed": removed,
        "message": (
            "Saved authentication token removed. Environment variables are not removed; "
            "unset RTM_AUTH_TOKEN yourself. Revoke access separately in Remember The Milk."
        ),
    }


def _open_browser(url: str) -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "webbrowser", "-t", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def authenticate(
    credentials: Credentials,
    backend: AuthBackend,
    force: bool = False,
    no_browser: bool = False,
) -> dict[str, object]:
    if not sys.stdin.isatty():
        raise MooError(
            "auth_requires_tty", "Authentication requires an interactive terminal.", 3
        )
    remaining = OPERATION_TIMEOUT

    def network_phase(operation: Callable[[], T]) -> T:
        nonlocal remaining
        started = monotonic()
        try:
            with deadline(remaining):
                return operation()
        except TimeoutError:
            raise MooError(
                "deadline_exceeded",
                "Operation deadline exceeded; no retry performed.",
                6,
                {"write_may_have_applied": False},
            ) from None
        finally:
            remaining -= monotonic() - started

    if credentials.token and not force:
        try:
            return network_phase(backend.check_token)
        except MooError as error:
            raise MooError(
                error.code,
                "Could not validate the existing token. "
                "Run moo auth --force to authenticate again.",
                error.exit_code,
            ) from None
    try:
        url = network_phase(backend.auth_url)
        print("Authorize moo in your browser:", file=sys.stderr)
        print(url, file=sys.stderr)
        if not no_browser:
            _open_browser(url)
        print(
            "After granting access, press Enter to continue (Ctrl-C to cancel).",
            file=sys.stderr,
        )
        input()
        token = network_phase(backend.exchange_token)
        if not token:
            raise MooError("auth_error", "Remember The Milk did not return a token.", 3)
        save_credentials(
            Credentials(credentials.api_key, credentials.shared_secret, token)
        )
    except (EOFError, KeyboardInterrupt):
        raise MooError(
            "cancelled", "Authentication cancelled; credentials were not changed.", 130
        ) from None
    return {"authenticated": True, "saved": True}

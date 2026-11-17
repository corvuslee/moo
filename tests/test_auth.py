import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from moo.auth import Credentials, authenticate, load_credentials, logout, save_credentials
from moo.contracts import MooError


@pytest.fixture(autouse=True)
def config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in (
        "RTM_API_KEY",
        "RTM_SHARED_SECRET",
        "RTM_AUTH_TOKEN",
        "DISPLAY",
        "WAYLAND_DISPLAY",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "private" / "credentials.json"
    monkeypatch.setenv("MOO_CONFIG", str(path))
    return path


@pytest.fixture
def backend() -> Mock:
    return Mock(
        auth_url=Mock(return_value="https://example.com/authorize?frob=test"),
        exchange_token=Mock(return_value="new-token"),
        check_token=Mock(return_value={"valid": True}),
    )


def test_save_and_load_private_config(config: Path) -> None:
    credentials = Credentials("key", "secret", "token")
    save_credentials(credentials)
    assert load_credentials() == credentials
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.parent.stat().st_mode & 0o777 == 0o700
    assert list(config.parent.iterdir()) == [config]


def test_existing_directory_mode_unchanged(config: Path) -> None:
    config.parent.mkdir(mode=0o755)
    save_credentials(Credentials("key", "secret"))
    assert config.parent.stat().st_mode & 0o777 == 0o755


def test_xdg_and_home_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MOO_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    save_credentials(Credentials("key", "secret", "token"))
    assert (tmp_path / "xdg/moo/credentials.json").is_file()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    with patch("moo.auth.Path.home", return_value=tmp_path):
        save_credentials(Credentials("other", "secret", "token"))
        assert load_credentials().api_key == "other"
    assert (tmp_path / ".config/moo/credentials.json").is_file()


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    save_credentials(Credentials("saved-key", "saved-secret", "saved-token"))
    monkeypatch.setenv("RTM_API_KEY", "env-key")
    monkeypatch.setenv("RTM_SHARED_SECRET", "env-secret")
    monkeypatch.setenv("RTM_AUTH_TOKEN", "env-token")
    assert load_credentials() == Credentials("env-key", "env-secret", "env-token")


@pytest.mark.parametrize("variable", ["RTM_API_KEY", "RTM_SHARED_SECRET"])
def test_changed_app_does_not_reuse_saved_token(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    save_credentials(Credentials("key", "secret", "token"))
    monkeypatch.setenv(variable, "different")
    assert load_credentials(require_token=False).token is None
    with pytest.raises(MooError) as caught:
        load_credentials()
    assert caught.value.exit_code == 3


def test_same_environment_app_reuses_token(monkeypatch: pytest.MonkeyPatch) -> None:
    save_credentials(Credentials("key", "secret", "token"))
    monkeypatch.setenv("RTM_API_KEY", "key")
    monkeypatch.setenv("RTM_SHARED_SECRET", "secret")
    assert load_credentials().token == "token"


@pytest.mark.parametrize("contents", ["{", "[]", '{"api_key": 123}', '{"token": false}'])
def test_invalid_config(config: Path, contents: str) -> None:
    config.parent.mkdir()
    config.write_text(contents)
    with pytest.raises(MooError) as caught:
        load_credentials(False)
    assert caught.value.exit_code == 3
    assert contents not in str(caught.value)


def test_missing_credentials_and_optional_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MooError) as caught:
        load_credentials()
    assert caught.value.exit_code == 3
    monkeypatch.setenv("RTM_API_KEY", "key")
    monkeypatch.setenv("RTM_SHARED_SECRET", "secret")
    assert load_credentials(False) == Credentials("key", "secret")
    with pytest.raises(MooError):
        load_credentials()


def test_atomic_write_failure_preserves_saved_credentials(config: Path) -> None:
    credentials = Credentials("key", "secret", "old")
    save_credentials(credentials)
    with patch("moo.auth.os.replace", side_effect=OSError("sensitive")):
        with pytest.raises(MooError) as caught:
            save_credentials(Credentials("key", "secret", "new"))
    assert caught.value.exit_code == 3
    assert "sensitive" not in str(caught.value)
    assert load_credentials() == credentials
    assert list(config.parent.iterdir()) == [config]


def test_logout_preserves_app_and_environment(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_credentials(Credentials("key", "secret", "token"))
    monkeypatch.setenv("RTM_AUTH_TOKEN", "environment-token")
    result = logout()
    assert result["token_removed"] is True
    assert json.loads(config.read_text()) == {"api_key": "key", "shared_secret": "secret"}
    assert os.environ["RTM_AUTH_TOKEN"] == "environment-token"
    assert "Environment" in str(result["message"])
    assert "Revoke" in str(result["message"])


def test_logout_without_config(config: Path) -> None:
    assert logout()["token_removed"] is False
    assert not config.exists()


def test_noninteractive_auth_never_calls_network(backend: Mock) -> None:
    with patch("moo.auth.sys.stdin.isatty", return_value=False):
        with pytest.raises(MooError) as caught:
            authenticate(Credentials("key", "secret", "token"), backend)
    assert caught.value.exit_code == 3
    assert backend.mock_calls == []


def test_existing_token_is_checked_without_replacement(backend: Mock) -> None:
    with patch("moo.auth.sys.stdin.isatty", return_value=True):
        assert authenticate(Credentials("key", "secret", "token"), backend) == {"valid": True}
    backend.check_token.assert_called_once_with()
    backend.auth_url.assert_not_called()


def test_invalid_token_instructs_force(backend: Mock) -> None:
    backend.check_token.side_effect = MooError("auth_error", "private error", 3)
    with patch("moo.auth.sys.stdin.isatty", return_value=True), pytest.raises(MooError) as caught:
        authenticate(Credentials("key", "secret", "token"), backend)
    assert "--force" in str(caught.value)
    backend.auth_url.assert_not_called()


def test_successful_auth_is_stderr_only(backend: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("moo.auth.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
        patch("moo.auth.subprocess.run") as browser,
    ):
        assert authenticate(Credentials("key", "secret"), backend)["authenticated"] is True
    assert load_credentials() == Credentials("key", "secret", "new-token")
    browser.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert backend.auth_url.return_value in output.err
    assert "secret" not in output.err
    assert "new-token" not in output.err


def test_force_replaces_existing_token(backend: Mock) -> None:
    save_credentials(Credentials("key", "secret", "old-token"))
    with (
        patch("moo.auth.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
    ):
        authenticate(load_credentials(), backend, force=True)
    backend.check_token.assert_not_called()
    assert load_credentials().token == "new-token"


def test_config_read_error(config: Path) -> None:
    config.mkdir(parents=True)
    with pytest.raises(MooError) as caught:
        load_credentials(False)
    assert caught.value.exit_code == 3


@pytest.mark.parametrize("cancel", [EOFError, KeyboardInterrupt])
def test_cancellation_preserves_credentials(backend: Mock, cancel: type[BaseException]) -> None:
    original = Credentials("key", "secret", "old-token")
    save_credentials(original)
    with (
        patch("moo.auth.sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=cancel),
        pytest.raises(MooError) as caught,
    ):
        authenticate(original, backend, force=True)
    assert caught.value.code == "cancelled"
    assert caught.value.exit_code == 130
    assert load_credentials() == original
    backend.exchange_token.assert_not_called()
    backend.check_token.assert_not_called()


@pytest.mark.parametrize("no_browser", [True, False])
def test_browser_is_bounded_and_optional(
    backend: Mock, monkeypatch: pytest.MonkeyPatch, no_browser: bool
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    with (
        patch("moo.auth.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
        patch(
            "moo.auth.subprocess.run", side_effect=subprocess.TimeoutExpired("browser", 5)
        ) as browser,
    ):
        authenticate(Credentials("key", "secret"), backend, no_browser=no_browser)
    if no_browser:
        browser.assert_not_called()
    else:
        assert browser.call_args.kwargs["timeout"] == 5
    assert load_credentials().token == "new-token"

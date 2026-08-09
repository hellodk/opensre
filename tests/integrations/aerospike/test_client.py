"""Tests for the Aerospike ``asinfo`` subprocess client.

No live ``asinfo`` binary and no network are needed for any test here —
``subprocess.run`` and ``shutil.which`` are mocked at the transport boundary,
mirroring ``tests/integrations/helm/test_client.py``.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from integrations.aerospike.client import (
    AsinfoBinaryNotFoundError,
    AsinfoConnectionError,
    AsinfoTimeoutError,
    _build_args,
    _resolved_asinfo_path,
    send_info_commands,
)


class _FakeConfig:
    def __init__(
        self,
        host: str = "node1",
        port: int = 3000,
        username: str = "",
        password: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds


def test_resolved_asinfo_path_uses_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.aerospike.client.shutil.which", lambda _name: "/usr/bin/asinfo"
    )
    assert _resolved_asinfo_path() == "/usr/bin/asinfo"


def test_resolved_asinfo_path_returns_none_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _name: None)
    assert _resolved_asinfo_path() is None


def test_build_args_includes_host_and_port() -> None:
    args = _build_args(_FakeConfig(host="node1", port=3000), ["statistics"])
    assert args == ["-h", "node1", "-p", "3000", "-v", "statistics"]


def test_build_args_joins_multiple_commands_with_newline() -> None:
    args = _build_args(_FakeConfig(), ["namespaces", "namespace/test"])
    assert args[-1] == "namespaces\nnamespace/test"


def test_build_args_adds_username_password_flags_when_both_set() -> None:
    args = _build_args(_FakeConfig(username="admin", password="secret"), ["status"])
    assert "-U" in args and "-P" in args
    assert args[args.index("-U") + 1] == "admin"
    assert args[args.index("-P") + 1] == "secret"


def test_build_args_omits_username_password_when_only_one_set() -> None:
    args = _build_args(_FakeConfig(username="admin", password=""), ["status"])
    assert "-U" not in args
    assert "-P" not in args


def test_send_info_commands_invokes_subprocess_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: "/bin/asinfo")
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("integrations.aerospike.client.subprocess.run", fake_run)
    config = _FakeConfig(timeout_seconds=7.5)

    result = send_info_commands(config, ["status"])

    assert result == {"status": "ok"}
    assert captured["timeout"] == 7.5


def test_send_info_commands_raises_on_binary_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: None)

    with pytest.raises(AsinfoBinaryNotFoundError):
        send_info_commands(_FakeConfig(), ["status"])


def test_send_info_commands_errors_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: "/bin/asinfo")

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr("integrations.aerospike.client.subprocess.run", fake_run)

    with pytest.raises(AsinfoConnectionError) as exc_info:
        send_info_commands(_FakeConfig(), ["status"])
    assert exc_info.value.returncode == 1
    assert "connection refused" in exc_info.value.stderr


def test_send_info_commands_errors_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: "/bin/asinfo")

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr("integrations.aerospike.client.subprocess.run", fake_run)

    with pytest.raises(AsinfoTimeoutError):
        send_info_commands(_FakeConfig(), ["status"])


def test_parse_multi_command_stdout_splits_on_name_tab_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: "/bin/asinfo")
    stdout = "statistics\tcluster_size=3;\nnamespaces\ttest;bar\n"

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("integrations.aerospike.client.subprocess.run", fake_run)

    result = send_info_commands(_FakeConfig(), ["statistics", "namespaces"])

    assert result == {"statistics": "cluster_size=3;", "namespaces": "test;bar"}


def test_parse_single_command_stdout_has_no_name_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.aerospike.client.shutil.which", lambda _n: "/bin/asinfo")

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("integrations.aerospike.client.subprocess.run", fake_run)

    result = send_info_commands(_FakeConfig(), ["status"])

    assert result == {"status": "ok"}

"""Tests for get_nginx_error_log (function-based, @tool decorated)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from integrations.nginx import MAX_LOG_TAIL_LINES
from integrations.nginx.logs import DEFAULT_LOG_TAIL_LINES
from integrations.nginx.tools.nginx_error_log_tool import (
    _NGINX_INJECTED,
    get_nginx_error_log,
)
from tests.tools.conftest import BaseToolContract

ERROR_LINE = (
    "2026/09/09 20:10:55 [error] 25#25: *5 connect() failed "
    "(111: Connection refused) while connecting to upstream, "
    'client: 172.17.0.1, server: , request: "GET /api/orders HTTP/1.1", '
    'upstream: "http://127.0.0.1:9/api/orders", host: "127.0.0.1:18080"'
)
NOTICE_LINE = '2026/09/09 20:10:53 [notice] 1#1: using the "epoll" event method'


class TestNginxErrorLogToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nginx_error_log.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nginx_error_log.__opensre_registered_tool__
    assert rt.name == "get_nginx_error_log"
    assert rt.source == "nginx"
    assert rt.injected_params == _NGINX_INJECTED
    properties = rt.public_input_schema.get("properties") or {}
    assert set(properties) == {"lines", "min_level", "contains"}


def test_run_happy_path() -> None:
    fake_result = {"source": "nginx", "available": True, "matched": 1}
    with patch(
        "integrations.nginx.tools.nginx_error_log_tool.get_error_log",
        return_value=fake_result,
    ):
        result = get_nginx_error_log(host="nginx.test")
    assert result["available"] is True


def test_full_path_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "error.log"
    target.write_text(f"{NOTICE_LINE}\n{ERROR_LINE}\n")
    result = get_nginx_error_log(host="nginx.test", error_log_path=str(target))
    assert result["available"] is True
    assert result["matched"] == 1
    assert result["level_counts"] == {"notice": 1, "error": 1}


def test_missing_file_unavailable(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope.log")
    result = get_nginx_error_log(host="nginx.test", error_log_path=missing)
    assert result["available"] is False
    assert missing in result["error"]


def test_lines_clamped(tmp_path: Path) -> None:
    target = tmp_path / "error.log"
    target.write_text(f"{ERROR_LINE}\n")
    huge = get_nginx_error_log(host="nginx.test", error_log_path=str(target), lines=99999)
    assert huge["lines_requested"] == MAX_LOG_TAIL_LINES
    zero = get_nginx_error_log(host="nginx.test", error_log_path=str(target), lines=0)
    assert zero["lines_requested"] == DEFAULT_LOG_TAIL_LINES

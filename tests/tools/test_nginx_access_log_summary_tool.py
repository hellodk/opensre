"""Tests for get_nginx_access_log_summary (function-based, @tool decorated)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from integrations.nginx import MAX_LOG_TAIL_LINES
from integrations.nginx.logs import DEFAULT_LOG_TAIL_LINES
from integrations.nginx.tools.nginx_access_log_summary_tool import (
    _NGINX_INJECTED,
    get_nginx_access_log_summary,
)
from tests.tools.conftest import BaseToolContract

ACCESS_LINE = (
    '172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "GET /api/orders HTTP/1.1" '
    '502 157 "-" "curl/8.5.0" 0.000'
)


class TestNginxAccessLogSummaryToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_nginx_access_log_summary.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_nginx_access_log_summary.__opensre_registered_tool__
    assert rt.name == "get_nginx_access_log_summary"
    assert rt.source == "nginx"
    assert rt.injected_params == _NGINX_INJECTED
    properties = rt.public_input_schema.get("properties") or {}
    assert set(properties) == {"lines"}


def test_run_happy_path() -> None:
    fake_result = {"source": "nginx", "available": True, "parsed": 1}
    with patch(
        "integrations.nginx.tools.nginx_access_log_summary_tool.get_access_log_summary",
        return_value=fake_result,
    ):
        result = get_nginx_access_log_summary(host="nginx.test")
    assert result["available"] is True


def test_full_path_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "access.log"
    target.write_text(f"{ACCESS_LINE}\n")
    result = get_nginx_access_log_summary(host="nginx.test", access_log_path=str(target))
    assert result["available"] is True
    assert result["parsed"] == 1
    assert result["status_classes"]["5xx"] == 1
    assert result["request_time"]["present"] is True


def test_missing_file_unavailable(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope.log")
    result = get_nginx_access_log_summary(host="nginx.test", access_log_path=missing)
    assert result["available"] is False
    assert missing in result["error"]


def test_lines_clamped(tmp_path: Path) -> None:
    target = tmp_path / "access.log"
    target.write_text(f"{ACCESS_LINE}\n")
    huge = get_nginx_access_log_summary(host="nginx.test", access_log_path=str(target), lines=99999)
    assert huge["lines_requested"] == MAX_LOG_TAIL_LINES
    zero = get_nginx_access_log_summary(host="nginx.test", access_log_path=str(target), lines=0)
    assert zero["lines_requested"] == DEFAULT_LOG_TAIL_LINES

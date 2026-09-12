"""Unit tests for nginx log tailing and parsing (access.log + error.log)."""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path

import pytest

from integrations.nginx import logs as nginx_logs
from integrations.nginx.logs import (
    DEFAULT_LOG_TAIL_LINES,
    MAX_LOG_TAIL_LINES,
    clamp_lines,
    open_log,
    parse_access_log_line,
    parse_error_log_line,
    summarize_access_log,
    summarize_error_log,
    tail_lines,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def error_lines() -> list[str]:
    return (FIXTURES / "error.log").read_text().splitlines()


@pytest.fixture
def access_lines() -> list[str]:
    return (FIXTURES / "access.log").read_text().splitlines()


@pytest.fixture
def combined_lines() -> list[str]:
    return (FIXTURES / "access_combined.log").read_text().splitlines()


# ---------------------------------------------------------------------------
# tail_lines / open_log / clamp_lines
# ---------------------------------------------------------------------------


class TestTailLines:
    def test_returns_last_n_in_order(self, tmp_path: Path) -> None:
        target = tmp_path / "big.log"
        target.write_text("".join(f"line {i}\n" for i in range(5000)))
        tailed = tail_lines(target, 200)
        assert len(tailed) == 200
        assert tailed[0] == "line 4800"
        assert tailed[-1] == "line 4999"

    def test_small_file_returns_all(self, tmp_path: Path) -> None:
        target = tmp_path / "small.log"
        target.write_text("a\nb\nc\n")
        assert tail_lines(target, 200) == ["a", "b", "c"]

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.log"
        target.write_text("")
        assert tail_lines(target, 200) == []

    def test_missing_trailing_newline_keeps_partial_line(self, tmp_path: Path) -> None:
        target = tmp_path / "partial.log"
        target.write_text("first\nsecond")
        assert tail_lines(target, 10) == ["first", "second"]

    def test_huge_file_bounded_by_bytes_and_max_lines(self, tmp_path: Path) -> None:
        target = tmp_path / "huge.log"
        with open(target, "w") as fh:
            for _ in range(20 * 1024 * 1024):
                fh.write("x\n")
        tailed = tail_lines(target, MAX_LOG_TAIL_LINES * 10)
        assert len(tailed) <= MAX_LOG_TAIL_LINES

    def test_multi_chunk_loop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(nginx_logs, "_TAIL_CHUNK_BYTES", 64)
        target = tmp_path / "chunked.log"
        target.write_text("".join(f"line {i}\n" for i in range(500)))
        tailed = tail_lines(target, 100)
        assert len(tailed) == 100
        assert tailed[0] == "line 400"
        assert tailed[-1] == "line 499"


class TestOpenLog:
    def test_missing(self, tmp_path: Path) -> None:
        path, err = open_log(str(tmp_path / "nope.log"))
        assert path is None
        assert err is not None
        assert "not found" in err

    def test_directory(self, tmp_path: Path) -> None:
        path, err = open_log(str(tmp_path))
        assert path is None
        assert err is not None
        assert "not a regular file" in err

    def test_unreadable(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("root bypasses file permission bits")
        target = tmp_path / "locked.log"
        target.write_text("x\n")
        target.chmod(0o000)
        try:
            path, err = open_log(str(target))
        finally:
            target.chmod(0o644)
        assert path is None
        assert err is not None
        assert "not readable" in err


class TestClampLines:
    def test_none_and_non_positive_yield_default(self) -> None:
        assert clamp_lines(None) == DEFAULT_LOG_TAIL_LINES
        assert clamp_lines(0) == DEFAULT_LOG_TAIL_LINES
        assert clamp_lines(-5) == DEFAULT_LOG_TAIL_LINES

    def test_over_max_clamped(self) -> None:
        assert clamp_lines(99999) == MAX_LOG_TAIL_LINES

    def test_in_range_passes_through(self) -> None:
        assert clamp_lines(50) == 50


# ---------------------------------------------------------------------------
# error.log
# ---------------------------------------------------------------------------


class TestParseErrorLogLine:
    def test_error_line_every_field(self, error_lines: list[str]) -> None:
        entry = parse_error_log_line(error_lines[-2])
        assert entry is not None
        assert entry.level == "error"
        assert entry.pid == 25
        assert entry.tid == 25
        assert entry.connection == 5
        assert entry.client == "172.17.0.1"
        assert entry.server == ""
        assert entry.request == "GET /api/orders HTTP/1.1"
        assert entry.upstream == "http://127.0.0.1:9/api/orders"
        assert entry.host == "127.0.0.1:18080"
        assert entry.message == (
            "connect() failed (111: Connection refused) while connecting to upstream"
        )

    def test_notice_line_without_connection(self, error_lines: list[str]) -> None:
        entry = parse_error_log_line(error_lines[0])
        assert entry is not None
        assert entry.level == "notice"
        assert entry.connection is None

    def test_garbage_returns_none(self) -> None:
        assert parse_error_log_line("this is not an nginx error line") is None
        assert parse_error_log_line("") is None


class TestSummarizeErrorLog:
    def test_default_warn_level(self, error_lines: list[str]) -> None:
        summary = summarize_error_log(error_lines)
        assert summary["lines_read"] == 24
        assert summary["unparsed_lines"] == 0
        assert summary["matched"] == 2
        assert summary["min_level"] == "warn"
        assert summary["level_counts"] == {"notice": 22, "error": 2}
        assert summary["top_messages"][0]["count"] == 2
        assert len(summary["entries"]) == 2

    def test_notice_level_matches_all(self, error_lines: list[str]) -> None:
        summary = summarize_error_log(error_lines, min_level="notice")
        assert summary["matched"] == 24

    def test_contains_filters_case_insensitively(self, error_lines: list[str]) -> None:
        summary = summarize_error_log(error_lines, contains="POST")
        assert summary["matched"] == 1

    def test_unknown_level_falls_back_to_warn(self, error_lines: list[str]) -> None:
        summary = summarize_error_log(error_lines, min_level="verbose")
        assert summary["min_level"] == "warn"
        assert summary["matched"] == 2

    def test_garbage_counted_not_raised(self, error_lines: list[str]) -> None:
        summary = summarize_error_log([*error_lines, "garbage line"])
        assert summary["unparsed_lines"] == 1
        assert summary["lines_read"] == 25


# ---------------------------------------------------------------------------
# access.log
# ---------------------------------------------------------------------------


class TestParseAccessLogLine:
    def test_timed_line(self, access_lines: list[str]) -> None:
        entry = parse_access_log_line(access_lines[4])
        assert entry is not None
        assert entry.method == "GET"
        assert entry.path == "/api/orders"
        assert entry.protocol == "HTTP/1.1"
        assert entry.status == HTTPStatus.BAD_GATEWAY
        assert entry.body_bytes_sent == 157
        assert entry.request_time == 0.0

    def test_combined_line_has_no_request_time(self, combined_lines: list[str]) -> None:
        entry = parse_access_log_line(combined_lines[4])
        assert entry is not None
        assert entry.status == HTTPStatus.BAD_GATEWAY
        assert entry.request_time is None

    def test_malformed_request(self) -> None:
        line = '172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "-" 400 0 "-" "curl/8.5.0"'
        entry = parse_access_log_line(line)
        assert entry is not None
        assert entry.method == ""
        assert entry.status == HTTPStatus.BAD_REQUEST

    def test_query_string_removed(self) -> None:
        line = (
            '172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "GET /a?b=1 HTTP/1.1" '
            '200 3 "-" "curl/8.5.0"'
        )
        entry = parse_access_log_line(line)
        assert entry is not None
        assert entry.path == "/a"

    def test_garbage_returns_none(self) -> None:
        assert parse_access_log_line("not a log line") is None


class TestSummarizeAccessLog:
    def test_timed_fixture(self, access_lines: list[str]) -> None:
        summary = summarize_access_log(access_lines)
        assert summary["lines_read"] == 8
        assert summary["unparsed_lines"] == 0
        assert summary["parsed"] == 8
        assert summary["status_classes"] == {
            "2xx": 6,
            "3xx": 0,
            "4xx": 0,
            "5xx": 2,
            "other": 0,
        }
        assert summary["top_paths_5xx"] == [{"path": "/api/orders", "count": 2}]
        assert summary["methods"] == {"GET": 6, "POST": 1, "HEAD": 1}
        assert summary["request_time"]["present"] is True
        assert summary["bytes_sent_total"] == 3 + 3 + 3 + 3 + 157 + 157 + 97 + 0
        assert summary["first_timestamp"] == "09/Sep/2026:20:10:51 +0000"
        assert summary["last_timestamp"] == "09/Sep/2026:20:10:57 +0000"

    def test_combined_fixture_has_no_request_time(self, combined_lines: list[str]) -> None:
        summary = summarize_access_log(combined_lines)
        assert summary["request_time"]["present"] is False
        assert summary["request_time"]["p50"] is None

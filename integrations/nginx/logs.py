"""Local nginx log reading: bounded tail, access.log + error.log parsing."""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_LOG_TAIL_LINES = 200
MAX_LOG_TAIL_LINES = 2000
_TAIL_CHUNK_BYTES = 64 * 1024
_MAX_TAIL_BYTES = 8 * 1024 * 1024
ERROR_LOG_LEVELS: tuple[str, ...] = (
    "debug",
    "info",
    "notice",
    "warn",
    "error",
    "crit",
    "alert",
    "emerg",
)
DEFAULT_MIN_ERROR_LEVEL = "warn"
TOP_N = 10


def open_log(path: str) -> tuple[Path | None, str | None]:
    """Resolve a log path; return (path, error) with error None on success."""
    candidate = Path(path)
    if not os.path.lexists(candidate):
        return None, f"log file not found: {path}"
    if not candidate.is_file():
        return None, f"log path is not a regular file: {path}"
    try:
        with open(candidate, "rb"):
            pass
    except OSError as exc:
        return None, f"log file not readable: {path} ({exc})"
    return candidate, None


def tail_lines(path: Path, max_lines: int) -> list[str]:
    """Return up to ``max_lines`` trailing lines, oldest first, bounded I/O."""
    limit = min(max_lines, MAX_LOG_TAIL_LINES) if max_lines > 0 else DEFAULT_LOG_TAIL_LINES
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        data = bytearray()
        newlines = 0
        while pos > 0 and newlines <= limit and len(data) < _MAX_TAIL_BYTES:
            step = min(_TAIL_CHUNK_BYTES, pos, _MAX_TAIL_BYTES - len(data))
            if step <= 0:
                break
            pos -= step
            handle.seek(pos)
            data[:0] = handle.read(step)
            newlines = data.count(b"\n")
    text = bytes(data).decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines[-limit:]


def clamp_lines(lines: int | None) -> int:
    """Bound a requested tail size to [1, MAX_LOG_TAIL_LINES] with a default."""
    if lines is None or lines <= 0:
        return DEFAULT_LOG_TAIL_LINES
    return min(lines, MAX_LOG_TAIL_LINES)


ERROR_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(?P<level>[a-z]+)\] (?P<pid>\d+)#(?P<tid>\d+): (?:\*(?P<connection>\d+) )?(?P<rest>.*)$"
)
_CONTEXT_RE = re.compile(
    r', (?P<key>client|server|request|upstream|host|referrer): (?P<value>"[^"]*"|[^,]*)'
)


@dataclass(frozen=True)
class ErrorLogEntry:
    """One parsed nginx error.log line."""

    timestamp: str
    level: str
    pid: int
    tid: int
    connection: int | None
    message: str
    client: str
    server: str
    request: str
    upstream: str
    host: str


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def parse_error_log_line(line: str) -> ErrorLogEntry | None:
    """Parse one error.log line; None when it does not match the grammar."""
    match = ERROR_LINE_RE.match(line or "")
    if match is None:
        return None
    rest = match.group("rest")
    message, _, _ = rest.partition(", client: ")
    context = {
        found.group("key"): _strip_quotes(found.group("value"))
        for found in _CONTEXT_RE.finditer(rest)
    }
    connection = match.group("connection")
    return ErrorLogEntry(
        timestamp=match.group("timestamp"),
        level=match.group("level"),
        pid=int(match.group("pid")),
        tid=int(match.group("tid")),
        connection=int(connection) if connection is not None else None,
        message=message,
        client=context.get("client", ""),
        server=context.get("server", ""),
        request=context.get("request", ""),
        upstream=context.get("upstream", ""),
        host=context.get("host", ""),
    )


def summarize_error_log(
    lines: list[str],
    *,
    min_level: str = DEFAULT_MIN_ERROR_LEVEL,
    contains: str = "",
) -> dict[str, Any]:
    """Summarize error.log lines: per-level counts plus filtered entries."""
    effective_level = min_level if min_level in ERROR_LOG_LEVELS else DEFAULT_MIN_ERROR_LEVEL
    threshold = ERROR_LOG_LEVELS.index(effective_level)
    needle = (contains or "").lower()

    parsed: list[tuple[str, ErrorLogEntry]] = []
    unparsed = 0
    for line in lines:
        entry = parse_error_log_line(line)
        if entry is None:
            unparsed += 1
            continue
        parsed.append((line, entry))

    level_counts: dict[str, int] = dict(Counter(entry.level for _, entry in parsed))

    matched = [
        (line, entry)
        for line, entry in parsed
        if entry.level in ERROR_LOG_LEVELS
        and ERROR_LOG_LEVELS.index(entry.level) >= threshold
        and (not needle or needle in line.lower())
    ]

    grouped: dict[str, dict[str, Any]] = {}
    for _, entry in matched:
        bucket = grouped.setdefault(
            entry.message, {"message": entry.message, "count": 0, "level": entry.level}
        )
        bucket["count"] += 1
    top_messages = sorted(grouped.values(), key=lambda item: item["count"], reverse=True)[:TOP_N]

    return {
        "lines_read": len(lines),
        "unparsed_lines": unparsed,
        "matched": len(matched),
        "min_level": effective_level,
        "contains": contains or "",
        "level_counts": level_counts,
        "top_messages": top_messages,
        "entries": [asdict(entry) for _, entry in matched],
    }


ACCESS_LINE_RE = re.compile(
    r"^(?P<remote_addr>\S+) - (?P<remote_user>\S+) \[(?P<time_local>[^\]]+)\] "
    r'"(?P<request>[^"]*)" (?P<status>\d{3}) (?P<body_bytes_sent>\d+|-) '
    r'"(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)"(?: (?P<request_time>\d+\.\d+))?'
)


@dataclass(frozen=True)
class AccessLogEntry:
    """One parsed nginx access.log (combined format, optional request_time)."""

    remote_addr: str
    remote_user: str
    time_local: str
    method: str
    path: str
    protocol: str
    status: int
    body_bytes_sent: int
    referer: str
    user_agent: str
    request_time: float | None


def _parse_body_bytes(raw: str) -> int:
    if raw == "-" or not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def parse_access_log_line(line: str) -> AccessLogEntry | None:
    """Parse one access.log line; None when it does not match the grammar."""
    match = ACCESS_LINE_RE.match(line or "")
    if match is None:
        return None
    request = match.group("request")
    parts = request.split()
    if len(parts) == 3:
        method, raw_path, protocol = parts
        path = raw_path.split("?", 1)[0]
    else:
        method, path, protocol = "", request, ""
    request_time = match.group("request_time")
    body_bytes = match.group("body_bytes_sent")
    return AccessLogEntry(
        remote_addr=match.group("remote_addr"),
        remote_user=match.group("remote_user"),
        time_local=match.group("time_local"),
        method=method,
        path=path,
        protocol=protocol,
        status=int(match.group("status")),
        body_bytes_sent=_parse_body_bytes(body_bytes),
        referer=match.group("referer"),
        user_agent=match.group("user_agent"),
        request_time=float(request_time) if request_time is not None else None,
    )


def _percentile(values: list[float], rank: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(rank * (len(ordered) - 1))))]


def summarize_access_log(lines: list[str]) -> dict[str, Any]:
    """Summarize access.log lines: status classes, top paths/ips, timings."""
    entries: list[AccessLogEntry] = []
    unparsed = 0
    for line in lines:
        entry = parse_access_log_line(line)
        if entry is None:
            unparsed += 1
            continue
        entries.append(entry)

    classes = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
    for entry in entries:
        bucket = f"{entry.status // 100}xx"
        if bucket in classes:
            classes[bucket] += 1
        else:
            classes["other"] += 1

    status_codes = [
        {"status": status, "count": count}
        for status, count in Counter(e.status for e in entries).most_common(TOP_N)
    ]
    methods = dict(Counter(e.method for e in entries))
    top_paths = [
        {"path": path, "count": count}
        for path, count in Counter(e.path for e in entries).most_common(TOP_N)
    ]
    top_paths_5xx = [
        {"path": path, "count": count}
        for path, count in Counter(e.path for e in entries if e.status >= 500).most_common(TOP_N)
    ]
    top_paths_4xx = [
        {"path": path, "count": count}
        for path, count in Counter(e.path for e in entries if 400 <= e.status < 500).most_common(
            TOP_N
        )
    ]
    top_client_ips = [
        {"remote_addr": addr, "count": count}
        for addr, count in Counter(e.remote_addr for e in entries).most_common(TOP_N)
    ]
    top_user_agents = [
        {"user_agent": agent, "count": count}
        for agent, count in Counter(e.user_agent for e in entries).most_common(TOP_N)
    ]
    timings = [e.request_time for e in entries if e.request_time is not None]
    slowest = sorted(
        (e for e in entries if e.request_time is not None),
        key=lambda e: e.request_time or 0.0,
        reverse=True,
    )[:TOP_N]

    return {
        "lines_read": len(lines),
        "unparsed_lines": unparsed,
        "parsed": len(entries),
        "status_classes": classes,
        "status_codes": status_codes,
        "methods": methods,
        "top_paths": top_paths,
        "top_paths_5xx": top_paths_5xx,
        "top_paths_4xx": top_paths_4xx,
        "top_client_ips": top_client_ips,
        "top_user_agents": top_user_agents,
        "bytes_sent_total": sum(e.body_bytes_sent for e in entries),
        "request_time": {
            "present": bool(timings),
            "p50": _percentile(timings, 0.5),
            "p95": _percentile(timings, 0.95),
            "max": max(timings) if timings else None,
            "slowest": [
                {
                    "path": e.path,
                    "method": e.method,
                    "status": e.status,
                    "request_time": e.request_time,
                }
                for e in slowest
            ],
        },
        "first_timestamp": entries[0].time_local if entries else "",
        "last_timestamp": entries[-1].time_local if entries else "",
    }

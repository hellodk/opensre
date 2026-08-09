"""Shared Aerospike integration helpers.

Provides configuration and read-only diagnostic queries for Aerospike
clusters via the ``asinfo`` CLI (see ``integrations/aerospike/client.py``).
All operations are read-only diagnostics — cluster/node/namespace health,
never record-level (KV) access. See
``docs/superpowers/specs/2026-08-09-aerospike-integration-design.md`` for
the full design.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from config.constants.aerospike import (
    AEROSPIKE_HOST_ENV,
    AEROSPIKE_PASSWORD_ENV,
    AEROSPIKE_PORT_ENV,
    AEROSPIKE_TLS_ENV,
    AEROSPIKE_USERNAME_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations._validation_helpers import report_classify_failure, report_validation_failure
from integrations.aerospike.client import (
    AsinfoBinaryNotFoundError,
    AsinfoConnectionError,
    AsinfoTimeoutError,
    send_info_commands,
)
from integrations.config_models import AerospikeIntegrationConfig
from platform.common.coercion import safe_int

logger = logging.getLogger(__name__)

DEFAULT_AEROSPIKE_PORT = 3000
DEFAULT_AEROSPIKE_TIMEOUT_SECONDS = 5.0
# Cap for namespace lists / latency buckets returned in a single tool response.
DEFAULT_AEROSPIKE_MAX_RESULTS = 50

_STATUS_COMMAND = "status"
_STATISTICS_COMMAND = "statistics"
_NAMESPACES_COMMAND = "namespaces"
_LATENCIES_COMMAND = "latencies:"

# asinfo/connectivity failures that are config/environment issues, not
# opensre bugs — surfaced as a friendly hint without a Sentry report.
_ASINFO_CLIENT_ERRORS = (AsinfoBinaryNotFoundError, AsinfoTimeoutError, AsinfoConnectionError)


class AerospikeConfig(StrictConfigModel):
    """Normalized Aerospike connection settings."""

    host: str = ""
    port: int = Field(default=DEFAULT_AEROSPIKE_PORT, ge=1, le=65535)
    username: str = ""
    password: str = ""
    timeout_seconds: float = Field(default=DEFAULT_AEROSPIKE_TIMEOUT_SECONDS, gt=0)
    tls_enabled: bool = False
    max_results: int = Field(default=DEFAULT_AEROSPIKE_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.host)


@dataclass(frozen=True)
class AerospikeValidationResult:
    """Result of validating an Aerospike integration."""

    ok: bool
    detail: str


def build_aerospike_config(raw: dict[str, Any] | None) -> AerospikeConfig:
    """Build a normalized Aerospike config object from env/store data."""
    return AerospikeConfig.model_validate(raw or {})


def aerospike_config_from_env() -> AerospikeConfig | None:
    """Load an Aerospike config from env vars."""
    host = os.getenv(AEROSPIKE_HOST_ENV, "").strip()
    if not host:
        return None

    return build_aerospike_config(
        {
            "host": host,
            "port": safe_int(
                os.getenv(AEROSPIKE_PORT_ENV, str(DEFAULT_AEROSPIKE_PORT)),
                DEFAULT_AEROSPIKE_PORT,
            ),
            "username": os.getenv(AEROSPIKE_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(AEROSPIKE_PASSWORD_ENV) or "",
            "tls_enabled": os.getenv(AEROSPIKE_TLS_ENV, "false").strip().lower()
            in ("true", "1", "yes"),
        }
    )


def validate_aerospike_config(config: AerospikeConfig) -> AerospikeValidationResult:
    """Validate Aerospike connectivity with a lightweight ``status`` info command."""
    if not config.host:
        return AerospikeValidationResult(ok=False, detail="Aerospike host is required.")

    try:
        raw = send_info_commands(config, [_STATUS_COMMAND])
    except _ASINFO_CLIENT_ERRORS as err:
        return AerospikeValidationResult(ok=False, detail=str(err))
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="aerospike",
            method="validate_aerospike_config",
        )
        return AerospikeValidationResult(ok=False, detail=f"Aerospike connection failed: {err}")

    status = raw.get(_STATUS_COMMAND, "").strip()
    if status != "ok":
        return AerospikeValidationResult(
            ok=False,
            detail=f"Aerospike status check returned an unexpected result: {status!r}",
        )
    return AerospikeValidationResult(
        ok=True,
        detail=f"Connected to Aerospike node at {config.host}:{config.port}; status: {status}.",
    )


def aerospike_is_available(sources: dict[str, dict]) -> bool:
    """Check if Aerospike integration params are present in available sources."""
    return bool(sources.get("aerospike", {}).get("host"))


def aerospike_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Aerospike connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so
    the LLM never needs to supply the host or password directly.
    """
    ae = sources.get("aerospike", {})
    return {
        "host": str(ae.get("host", "")).strip(),
        "port": int(ae.get("port", DEFAULT_AEROSPIKE_PORT) or DEFAULT_AEROSPIKE_PORT),
        "username": str(ae.get("username", "")).strip(),
        "password": str(ae.get("password", "")).strip(),
    }


# ---------------------------------------------------------------------------
# Response-grammar parsers (design doc §1.2/§3.2) — pure functions, no
# subprocess/transport concerns; those live in client.py.
# ---------------------------------------------------------------------------


def _parse_semicolon_kv(raw: str) -> dict[str, str]:
    """Parse ``k1=v1;k2=v2;`` into a dict.

    Ignores a trailing empty segment and any malformed segment missing
    ``=`` (real clusters sometimes emit odd diagnostic strings in edge
    fields) rather than raising.
    """
    result: dict[str, str] = {}
    for segment in raw.split(";"):
        if not segment:
            continue
        key, sep, value = segment.partition("=")
        if not sep:
            continue
        result[key] = value
    return result


def _parse_semicolon_list(raw: str) -> list[str]:
    """Parse ``a;b;c`` into ``['a', 'b', 'c']``, dropping empty segments."""
    return [segment for segment in raw.split(";") if segment]


_LATENCY_HEADER_RE = re.compile(r"^\{?([\w.-]+)\}?-(\w+):(.+)$")


def _coerce_latency_value(value: str) -> Any:
    """Best-effort numeric coercion for a latency bucket value; falls back to ``str``."""
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _parse_latency(raw: str) -> dict[str, Any]:
    """Best-effort parse of the ``latencies:`` (or legacy ``latency:``) payload.

    Recognizes segments shaped ``{namespace}-<op>:<column1>,<column2>,...``
    (a histogram header) followed by comma-separated data rows, joined by
    ``;`` or newline (both are observed across ``asinfo`` builds/server
    versions). Degrades to ``{"raw": raw}`` on any shape this parser doesn't
    recognize rather than raising — the exact cross-version grammar is
    unconfirmed against a live server (design doc §9 item 5).
    """
    text = (raw or "").strip()
    if not text:
        return {"raw": raw}

    segments = [seg for seg in re.split(r"[;\n]", text) if seg.strip()]
    histograms: dict[str, list[dict[str, Any]]] = {}
    current_op: str | None = None
    current_columns: list[str] = []

    for segment in segments:
        header_match = _LATENCY_HEADER_RE.match(segment)
        if header_match:
            current_op = header_match.group(2)
            current_columns = [c.strip() for c in header_match.group(3).split(",") if c.strip()]
            histograms.setdefault(current_op, [])
            continue

        if current_op is None:
            # Data encountered before any recognized histogram header — an
            # unrecognized shape overall.
            return {"raw": raw}

        values = [v.strip() for v in segment.split(",") if v.strip() != ""]
        if not values:
            continue
        row: dict[str, Any] = {}
        for index, value in enumerate(values):
            key = current_columns[index] if index < len(current_columns) else f"field_{index}"
            row[key] = _coerce_latency_value(value)
        histograms[current_op].append(row)

    if not histograms:
        return {"raw": raw}
    return {"histograms": histograms}


# ---------------------------------------------------------------------------
# Tool-facing normalization functions
# ---------------------------------------------------------------------------


def get_node_status(config: AerospikeConfig) -> dict[str, Any]:
    """Retrieve node health: uptime, cluster size, cluster key, and status.

    Read-only: issues the ``status`` and ``statistics`` info commands,
    batched into a single ``asinfo`` invocation.
    """
    if not config.is_configured:
        return tool_unavailable("aerospike", "Not configured.")

    try:
        raw = send_info_commands(config, [_STATUS_COMMAND, _STATISTICS_COMMAND])
    except Exception as err:
        return _aerospike_error(err, "get_node_status")

    node_status = raw.get(_STATUS_COMMAND, "").strip()
    stats = _parse_semicolon_kv(raw.get(_STATISTICS_COMMAND, ""))
    return {
        "source": "aerospike",
        "available": True,
        "node_status": node_status,
        "cluster_size": safe_int(stats.get("cluster_size", 0), 0),
        "cluster_key": stats.get("cluster_key", ""),
        "uptime_seconds": safe_int(stats.get("uptime", 0), 0),
        "cluster_integrity": stats.get("cluster_integrity", "").lower() == "true",
    }


def get_namespace_stats(config: AerospikeConfig, namespace: str = "") -> dict[str, Any]:
    """List Aerospike namespaces and per-namespace storage/memory/object stats.

    Read-only: issues ``namespaces`` (skipped when ``namespace`` is given)
    plus one ``namespace/<ns>`` command per namespace, batched into a single
    ``asinfo`` invocation.

    **These figures are single-node/local, not cluster-wide aggregates** —
    a node's ``namespace/<ns>`` response reflects only that node's local
    partition share (roughly ``1/N`` of the master partitions in an
    N-node cluster). This tool does not fan out to every cluster member and
    sum; the ``scope`` field in the response makes this explicit.
    """
    if not config.is_configured:
        return tool_unavailable("aerospike", "Not configured.")

    namespace = str(namespace or "").strip()
    try:
        if namespace:
            names = [namespace]
        else:
            ns_raw = send_info_commands(config, [_NAMESPACES_COMMAND])
            names = _parse_semicolon_list(ns_raw.get(_NAMESPACES_COMMAND, ""))

        truncated = len(names) > config.max_results
        names = names[: config.max_results]

        namespace_stats: dict[str, Any] = {}
        if names:
            ns_commands = [f"namespace/{name}" for name in names]
            raw = send_info_commands(config, ns_commands)
            for name, command in zip(names, ns_commands, strict=True):
                parsed = _parse_semicolon_kv(raw.get(command, ""))
                namespace_stats[name] = {
                    "objects": safe_int(parsed.get("objects", 0), 0),
                    "memory_used_bytes": safe_int(parsed.get("memory_used_bytes", 0), 0),
                    "device_used_bytes": safe_int(parsed.get("device_used_bytes", 0), 0),
                    "replication_factor": safe_int(
                        parsed.get("effective_replication_factor", parsed.get("repl-factor", 0)),
                        0,
                    ),
                    "stop_writes": parsed.get("stop_writes", "").lower() == "true",
                }
    except Exception as err:
        return _aerospike_error(err, "get_namespace_stats")

    return {
        "source": "aerospike",
        "available": True,
        "scope": "single_node",
        "namespace_count": len(namespace_stats),
        "namespaces": namespace_stats,
        "truncated": truncated,
    }


def get_latency(config: AerospikeConfig) -> dict[str, Any]:
    """Retrieve Aerospike latency histograms (read/write/udf/query buckets).

    Read-only: issues the modern ``latencies:`` info command.
    ``_parse_latency`` degrades to a ``raw`` payload (with
    ``available: True`` — data was fetched, just not fully parsed) when the
    response shape isn't recognized, e.g. an older server that only exposes
    the legacy ``latency:`` grammar.
    """
    if not config.is_configured:
        return tool_unavailable("aerospike", "Not configured.")

    try:
        raw = send_info_commands(config, [_LATENCIES_COMMAND])
    except Exception as err:
        return _aerospike_error(err, "get_latency")

    parsed = _parse_latency(raw.get(_LATENCIES_COMMAND, ""))
    if "raw" in parsed:
        return {
            "source": "aerospike",
            "available": True,
            "raw": parsed["raw"],
            "parse_warning": (
                "Latency response did not match the expected latencies: grammar; "
                "returning the raw payload."
            ),
        }
    return {
        "source": "aerospike",
        "available": True,
        "histograms": parsed["histograms"],
    }


def _aerospike_error(err: Exception, method: str) -> dict[str, Any]:
    """Normalize an Aerospike client exception into a graceful, available=False payload.

    ``asinfo`` binary-not-found, timeout, and connection/command failures
    return a friendly hint without a Sentry report (config/environment
    issues, not opensre bugs); anything else is reported for diagnosis.
    Mirrors ``_redis_error()``.
    """
    if isinstance(err, _ASINFO_CLIENT_ERRORS):
        return tool_unavailable("aerospike", str(err))
    report_validation_failure(
        err,
        logger=logger,
        integration="aerospike",
        method=method,
    )
    return tool_unavailable("aerospike", str(err))


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[AerospikeIntegrationConfig | None, str | None]:
    try:
        cfg = AerospikeIntegrationConfig.model_validate(
            {
                "host": credentials.get("host", ""),
                "port": credentials.get("port", DEFAULT_AEROSPIKE_PORT),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "timeout_seconds": credentials.get(
                    "timeout_seconds", DEFAULT_AEROSPIKE_TIMEOUT_SECONDS
                ),
                "tls_enabled": credentials.get("tls_enabled", False),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="aerospike", record_id=record_id)
        return None, None
    if cfg.host:
        return cfg, "aerospike"
    return None, None

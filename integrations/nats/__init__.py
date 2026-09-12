"""Shared NATS integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for nats-server via its HTTP monitoring endpoint (port 8222 by
default). All operations are read-only: server status, connections,
subscriptions, JetStream streams/consumers, and cluster topology.
"""

from __future__ import annotations

from integrations.nats.config import (
    DEFAULT_NATS_TIMEOUT_SECONDS,
    NatsConfig,
    build_nats_config,
    classify,
    nats_config_from_env,
    nats_extract_params,
    nats_is_available,
)
from integrations.nats.diagnostics import (
    ALLOWED_CONNZ_SORT,
    ALLOWED_CONNZ_STATE,
    CLOSED_ONLY_SORTS,
    DEFAULT_CONNECTION_LIMIT,
    MAX_CONNECTION_LIMIT,
    get_cluster_status,
    get_connections,
    get_jetstream_consumers,
    get_jetstream_streams,
    get_server_status,
    get_subscriptions,
)
from integrations.nats.validation import NatsValidationResult, validate_nats_config

__all__ = [
    "ALLOWED_CONNZ_SORT",
    "ALLOWED_CONNZ_STATE",
    "CLOSED_ONLY_SORTS",
    "DEFAULT_CONNECTION_LIMIT",
    "DEFAULT_NATS_TIMEOUT_SECONDS",
    "MAX_CONNECTION_LIMIT",
    "NatsConfig",
    "NatsValidationResult",
    "build_nats_config",
    "classify",
    "get_cluster_status",
    "get_connections",
    "get_jetstream_consumers",
    "get_jetstream_streams",
    "get_server_status",
    "get_subscriptions",
    "nats_config_from_env",
    "nats_extract_params",
    "nats_is_available",
    "validate_nats_config",
]

"""Validation for the NATS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus

import integrations.nats.client as nats_client
from integrations._validation_helpers import report_validation_failure
from integrations.nats.config import NatsConfig
from integrations.nats.monitoring import shape_varz

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NatsValidationResult:
    """Result of validating a NATS integration."""

    ok: bool
    detail: str


def validate_nats_config(config: NatsConfig) -> NatsValidationResult:
    """Validate NATS monitoring-endpoint reachability with lightweight calls."""
    if not config.is_configured:
        return NatsValidationResult(
            ok=False,
            detail="NATS monitoring URL is required (NATS_MONITOR_URL).",
        )
    try:
        with nats_client.build_client(config) as client:
            health_result, health_err = nats_client.get_json(
                client,
                config,
                "/healthz",
                accept=(HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE),
            )
            if health_err is not None or health_result is None:
                message = (
                    health_err.message if health_err is not None else "empty /healthz response"
                )
                return NatsValidationResult(ok=False, detail=message)
            if health_result.status == HTTPStatus.SERVICE_UNAVAILABLE:
                body = health_result.payload if isinstance(health_result.payload, dict) else {}
                return NatsValidationResult(
                    ok=False,
                    detail=(
                        f"NATS server at {config.url} reports "
                        f"{body.get('status', 'unavailable')}: "
                        f"{body.get('error', '')}"
                    ),
                )
            varz_result, varz_err = nats_client.get_json(client, config, "/varz")
            if varz_err is not None or varz_result is None:
                message = varz_err.message if varz_err is not None else "empty /varz response"
                return NatsValidationResult(ok=False, detail=message)
            varz = shape_varz(varz_result.payload)
            payload = varz_result.payload
            js_enabled = bool(payload.get("jetstream"))
            raw_cluster = payload.get("cluster") or {}
            cname = raw_cluster.get("name", "standalone") if raw_cluster else "standalone"
            return NatsValidationResult(
                ok=True,
                detail=(
                    f"nats-server {varz['version']} '{varz['server_name']}' "
                    f"reachable at {config.url} (uptime {varz['uptime']}, "
                    f"{varz['connections']} client connections, "
                    f"JetStream {'enabled' if js_enabled else 'disabled'}, "
                    f"cluster {cname})"
                ),
            )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="nats",
            method="validate_nats_config",
        )
        return NatsValidationResult(ok=False, detail=f"NATS connection failed: {err}")

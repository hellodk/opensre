"""NATS Subscriptions Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.nats import (
    NatsConfig,
    get_subscriptions,
    nats_extract_params,
    nats_is_available,
)


def _map_get_nats_subscriptions(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite subscription totals or the match count for a subject."""
    if not output.get("available"):
        return
    if output.get("subject"):
        summary = f"{output.get('match_count', 0)} subscription(s) match {output.get('subject')}"
    else:
        stats = output.get("stats") or {}
        summary_data = output.get("summary") or {}
        top = summary_data.get("top_by_msgs") or []
        summary = (
            f"{stats.get('num_subscriptions', 0)} subscription(s), "
            f"cache hit rate {stats.get('cache_hit_rate', 0.0):.0%}"
        )
        if top:
            summary += f", busiest: {top[0].get('subject')} ({top[0].get('msgs')})"
    record_evidence_entry(
        evidence,
        source="get_nats_subscriptions",
        label="NATS Subscriptions",
        summary=summary,
    )


@tool(
    name="get_nats_subscriptions",
    description="Return subscription statistics for one nats-server (total, sublist cache hit rate, fan-out) and the busiest application subjects by message count. With a subject, list only the subscriptions whose pattern matches it, to check whether anyone is listening on that subject.",
    source="nats",
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Confirming a service is subscribed to the subject it should consume",
        "Spotting the busiest subjects when message volume looks wrong",
        "Checking sublist cache efficiency on a server with many subscriptions",
    ],
    is_available=nats_is_available,
    injected_params=("url", "username", "password", "verify_ssl"),
    extract_params=nats_extract_params,
    evidence_mapper=_map_get_nats_subscriptions,
)
def get_nats_subscriptions(
    url: str,
    username: str = "",
    password: str = "",
    verify_ssl: bool = True,
    subject: str = "",
) -> dict[str, Any]:
    """Return subscription statistics for one nats-server."""
    config = NatsConfig(
        url=url,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return get_subscriptions(config, subject=subject)

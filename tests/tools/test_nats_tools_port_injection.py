"""NATS tools must not expose connection params to the LLM.

``url``, ``username``, ``password`` and ``verify_ssl`` are resolved from
integration config (env/store) like credentials. When the model can set
them, it can clobber the resolved value with the schema default and the
tool connects to the wrong endpoint.
"""

from __future__ import annotations

import inspect

import pytest

from integrations.nats.tools.nats_cluster_status_tool import (
    get_nats_cluster_status,
)
from integrations.nats.tools.nats_connections_tool import get_nats_connections
from integrations.nats.tools.nats_jetstream_consumers_tool import (
    get_nats_jetstream_consumers,
)
from integrations.nats.tools.nats_jetstream_streams_tool import (
    get_nats_jetstream_streams,
)
from integrations.nats.tools.nats_server_status_tool import get_nats_server_status
from integrations.nats.tools.nats_subscriptions_tool import get_nats_subscriptions

_NATS_TOOLS = [
    get_nats_server_status,
    get_nats_connections,
    get_nats_subscriptions,
    get_nats_jetstream_streams,
    get_nats_jetstream_consumers,
    get_nats_cluster_status,
]

_NATS_INJECTED = ("url", "username", "password", "verify_ssl")


@pytest.mark.parametrize("fn", _NATS_TOOLS, ids=lambda fn: fn.__name__)
@pytest.mark.parametrize("param", _NATS_INJECTED)
def test_injected_not_exposed_to_llm(fn, param: str) -> None:
    rt = fn.__opensre_registered_tool__
    assert param in rt.injected_params
    properties = rt.public_input_schema.get("properties") or {}
    assert param not in properties


@pytest.mark.parametrize("fn", _NATS_TOOLS, ids=lambda fn: fn.__name__)
@pytest.mark.parametrize("param", _NATS_INJECTED)
def test_injected_still_callable_directly(fn, param: str) -> None:
    """Injected params remain real parameters (framework fills them from
    config); only their visibility to the LLM changes."""
    assert param in inspect.signature(fn).parameters


def test_password_never_in_schema() -> None:
    for fn in _NATS_TOOLS:
        rt = fn.__opensre_registered_tool__
        assert "password" not in str(rt.public_input_schema)

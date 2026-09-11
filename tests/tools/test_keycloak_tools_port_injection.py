"""Connection params stay injected for every Keycloak tool.

``url``, ``management_url``, ``realm``, ``auth_realm``, ``client_id``,
``client_secret`` and ``verify_ssl`` are resolved from integration config
(env/store) like credentials. When the model can set them, it can clobber the
resolved value with a schema default and the tool talks to the wrong server
(mirrors the port-clobbering incident behind the aerospike port test).
"""

from __future__ import annotations

import inspect

import pytest

from integrations.keycloak.tools.keycloak_admin_events_tool import (
    get_keycloak_admin_events,
)
from integrations.keycloak.tools.keycloak_client_sessions_tool import (
    get_keycloak_client_sessions,
)
from integrations.keycloak.tools.keycloak_login_failures_tool import (
    get_keycloak_login_failures,
)
from integrations.keycloak.tools.keycloak_realm_overview_tool import (
    get_keycloak_realm_overview,
)
from integrations.keycloak.tools.keycloak_server_status_tool import (
    get_keycloak_server_status,
)
from integrations.keycloak.tools.keycloak_user_status_tool import (
    get_keycloak_user_status,
)

_KEYCLOAK_TOOLS = [
    get_keycloak_server_status,
    get_keycloak_realm_overview,
    get_keycloak_login_failures,
    get_keycloak_admin_events,
    get_keycloak_client_sessions,
    get_keycloak_user_status,
]

_KEYCLOAK_INJECTED = (
    "url",
    "management_url",
    "realm",
    "auth_realm",
    "client_id",
    "client_secret",
    "verify_ssl",
)


@pytest.mark.parametrize("injected", _KEYCLOAK_INJECTED)
@pytest.mark.parametrize("fn", _KEYCLOAK_TOOLS, ids=lambda fn: fn.__name__)
def test_connection_param_not_exposed_to_llm(fn, injected: str) -> None:
    rt = fn.__opensre_registered_tool__
    assert injected in rt.injected_params
    properties = rt.public_input_schema.get("properties") or {}
    assert injected not in properties


@pytest.mark.parametrize("injected", _KEYCLOAK_INJECTED)
@pytest.mark.parametrize("fn", _KEYCLOAK_TOOLS, ids=lambda fn: fn.__name__)
def test_connection_param_still_callable_directly(fn, injected: str) -> None:
    """Injected params remain real parameters (the framework fills them from
    config); only their visibility to the LLM changes."""
    assert injected in inspect.signature(fn).parameters


@pytest.mark.parametrize("fn", _KEYCLOAK_TOOLS, ids=lambda fn: fn.__name__)
def test_client_secret_never_in_schema(fn) -> None:
    rt = fn.__opensre_registered_tool__
    assert "client_secret" not in str(rt.public_input_schema)

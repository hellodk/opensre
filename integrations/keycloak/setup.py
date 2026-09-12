"""What Keycloak needs before it is considered configured."""

from __future__ import annotations

from config.constants.keycloak import (
    KEYCLOAK_AUTH_REALM_ENV,
    KEYCLOAK_CLIENT_ID_ENV,
    KEYCLOAK_CLIENT_SECRET_ENV,
    KEYCLOAK_MANAGEMENT_URL_ENV,
    KEYCLOAK_REALM_ENV,
    KEYCLOAK_URL_ENV,
    KEYCLOAK_VERIFY_SSL_ENV,
)
from integrations.keycloak.verifier import verify_keycloak
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
REALM_FIELD = "realm"
CLIENT_ID_FIELD = "client_id"
CLIENT_SECRET_FIELD = "client_secret"
AUTH_REALM_FIELD = "auth_realm"
MANAGEMENT_URL_FIELD = "management_url"
VERIFY_SSL_FIELD = "verify_ssl"

KEYCLOAK_SETUP = IntegrationSetupSpec(
    service="keycloak",
    fields=(
        SetupField(
            name=URL_FIELD,
            label="Server URL",
            prompt="Keycloak base URL (e.g. https://sso.example.net)",
            env_var=KEYCLOAK_URL_ENV,
        ),
        SetupField(
            name=REALM_FIELD,
            label="Realm",
            prompt="Realm to diagnose (e.g. myapp)",
            env_var=KEYCLOAK_REALM_ENV,
        ),
        SetupField(
            name=CLIENT_ID_FIELD,
            label="Client ID",
            prompt="Confidential client with service accounts enabled (e.g. opensre)",
            env_var=KEYCLOAK_CLIENT_ID_ENV,
        ),
        SetupField(
            name=CLIENT_SECRET_FIELD,
            label="Client secret",
            prompt="Client secret (Clients > opensre > Credentials)",
            env_var=KEYCLOAK_CLIENT_SECRET_ENV,
            secret=True,
        ),
        SetupField(
            name=AUTH_REALM_FIELD,
            label="Token realm",
            prompt="Realm the client lives in (leave blank if same as Realm)",
            env_var=KEYCLOAK_AUTH_REALM_ENV,
            required=False,
        ),
        SetupField(
            name=MANAGEMENT_URL_FIELD,
            label="Management URL",
            prompt=(
                "Management interface URL for health/metrics "
                "(e.g. http://sso.example.net:9000; leave blank to skip)"
            ),
            env_var=KEYCLOAK_MANAGEMENT_URL_ENV,
            required=False,
        ),
        SetupField(
            name=VERIFY_SSL_FIELD,
            label="Verify TLS certificate",
            prompt="Verify the TLS certificate? (true/false)",
            env_var=KEYCLOAK_VERIFY_SSL_ENV,
            default="true",
            required=False,
        ),
    ),
    verify=verify_keycloak,
)

__all__ = [
    "AUTH_REALM_FIELD",
    "CLIENT_ID_FIELD",
    "CLIENT_SECRET_FIELD",
    "KEYCLOAK_SETUP",
    "MANAGEMENT_URL_FIELD",
    "REALM_FIELD",
    "URL_FIELD",
    "VERIFY_SSL_FIELD",
]

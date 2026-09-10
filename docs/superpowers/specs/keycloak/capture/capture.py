"""Provision a throwaway Keycloak and capture every payload the spec embeds.

Run from the opensre repo root (httpx comes from the project venv):

    uv run python docs/superpowers/specs/keycloak/capture/capture.py

Expects the container from run.sh listening on 127.0.0.1:18080 (HTTP) and
127.0.0.1:19000 (management: health + metrics).
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:18080"
MGMT = "http://127.0.0.1:19000"
REALM = "opensre-demo"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
SA_CLIENT_ID = "opensre"
SA_CLIENT_SECRET = "opensre-service-secret"
OUT = pathlib.Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
INDEX: dict[str, dict[str, Any]] = {}


def save(name: str, resp: httpx.Response, *, redact: tuple[str, ...] = ()) -> Any:
    """Persist body verbatim (JSON pretty-printed) plus method/url/status in index.json."""
    parsed: Any = None
    text = resp.text
    try:
        parsed = resp.json()
    except ValueError:
        parsed = None
    if parsed is not None:
        if isinstance(parsed, dict):
            for key in redact:
                if key in parsed:
                    parsed[key] = "<redacted>"
        text = json.dumps(parsed, indent=2, sort_keys=False) + "\n"
    (OUT / name).write_text(text)
    INDEX[name] = {
        "method": resp.request.method,
        "url": str(resp.request.url),
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
    }
    print(f"{resp.status_code:>3} {resp.request.method:<4} {resp.request.url} -> {name}")
    return parsed


def wait_ready(client: httpx.Client) -> None:
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            r = client.get(f"{MGMT}/health/ready")
            if r.status_code == 200:
                print("keycloak ready")
                return
        except httpx.RequestError:
            pass
        time.sleep(3)
    sys.exit("keycloak did not become ready in 240s")


def token(client: httpx.Client, realm: str, data: dict[str, str]) -> httpx.Response:
    return client.post(
        f"{BASE}/realms/{realm}/protocol/openid-connect/token",
        data=data,
        headers={"Accept": "application/json"},
    )


def admin_token(client: httpx.Client) -> str:
    r = token(
        client,
        "master",
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> None:
    with httpx.Client(timeout=30.0) as client:
        wait_ready(client)
        admin = {"Authorization": f"Bearer {admin_token(client)}"}
        adm = f"{BASE}/admin/realms"

        # ---- provisioning (admin token) -------------------------------------
        r = client.post(
            adm,
            headers=admin,
            json={
                "realm": REALM,
                "displayName": "OpenSRE demo",
                "enabled": True,
                "sslRequired": "external",
                "eventsEnabled": True,
                "eventsExpiration": 604800,
                "adminEventsEnabled": True,
                "adminEventsDetailsEnabled": True,
                "bruteForceProtected": True,
                "failureFactor": 3,
                "permanentLockout": False,
            },
        )
        print("create realm", r.status_code)
        r = client.post(
            f"{adm}/{REALM}/clients",
            headers=admin,
            json={
                "clientId": SA_CLIENT_ID,
                "name": "OpenSRE",
                "secret": SA_CLIENT_SECRET,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
            },
        )
        print("create sa client", r.status_code, r.headers.get("location"))
        sa_client_id = r.headers["location"].rsplit("/", 1)[-1]
        sa_user = client.get(
            f"{adm}/{REALM}/clients/{sa_client_id}/service-account-user", headers=admin
        ).json()
        rm = client.get(
            f"{adm}/{REALM}/clients", params={"clientId": "realm-management"}, headers=admin
        ).json()[0]
        roles = client.get(f"{adm}/{REALM}/clients/{rm['id']}/roles", headers=admin).json()
        wanted = {"view-realm", "view-users", "view-clients", "view-events"}
        picked = [{"id": x["id"], "name": x["name"]} for x in roles if x["name"] in wanted]
        r = client.post(
            f"{adm}/{REALM}/users/{sa_user['id']}/role-mappings/clients/{rm['id']}",
            headers=admin,
            json=picked,
        )
        print("assign roles", r.status_code, sorted(x["name"] for x in picked))
        r = client.post(
            f"{adm}/{REALM}/clients",
            headers=admin,
            json={
                "clientId": "demo-app",
                "name": "Demo app",
                "protocol": "openid-connect",
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": True,
            },
        )
        print("create demo-app", r.status_code)
        users = [
            {
                "username": "alice",
                "email": "alice@example.com",
                "firstName": "Alice",
                "lastName": "Example",
                "enabled": True,
                "emailVerified": True,
                "credentials": [{"type": "password", "value": "alice-pass", "temporary": False}],
            },
            {
                "username": "bob",
                "email": "bob@example.com",
                "firstName": "Bob",
                "lastName": "Disabled",
                "enabled": False,
                "emailVerified": True,
                "credentials": [{"type": "password", "value": "bob-pass", "temporary": False}],
            },
            {
                "username": "carol",
                "email": "carol@example.com",
                "firstName": "Carol",
                "lastName": "Locked",
                "enabled": True,
                "emailVerified": False,
                "credentials": [{"type": "password", "value": "carol-pass", "temporary": False}],
            },
            {
                "username": "dave",
                "email": "dave@example.com",
                "firstName": "Dave",
                "lastName": "Pending",
                "enabled": True,
                "emailVerified": False,
                "requiredActions": ["VERIFY_EMAIL", "UPDATE_PASSWORD"],
                "credentials": [{"type": "password", "value": "dave-pass", "temporary": False}],
            },
        ]
        ids: dict[str, str] = {}
        for u in users:
            r = client.post(f"{adm}/{REALM}/users", headers=admin, json=u)
            ids[u["username"]] = r.headers["location"].rsplit("/", 1)[-1]
            print("create user", u["username"], r.status_code)

        # ---- generate login events ------------------------------------------
        def login(username: str, password: str, name: str) -> None:
            r = token(
                client,
                REALM,
                {
                    "grant_type": "password",
                    "client_id": "demo-app",
                    "username": username,
                    "password": password,
                },
            )
            save(name, r, redact=("access_token", "refresh_token", "id_token"))
            time.sleep(1.1)

        login("alice", "alice-pass", "login_alice_ok.json")
        for i in range(1, 5):
            login("carol", "wrong-password", f"login_carol_fail_{i}.json")
        login("mallory", "whatever", "login_unknown_user.json")
        login("bob", "bob-pass", "login_disabled_user.json")
        login("dave", "dave-pass", "login_required_action_user.json")

        # ---- service-account token (the credential opensre will use) --------
        r = token(
            client,
            REALM,
            {
                "grant_type": "client_credentials",
                "client_id": SA_CLIENT_ID,
                "client_secret": SA_CLIENT_SECRET,
            },
        )
        sa_tok = save("token_client_credentials.json", r, redact=("access_token",))
        sa = {"Authorization": f"Bearer {r.json()['access_token']}"}

        # ---- token failure shapes ------------------------------------------
        save(
            "token_error_bad_secret.json",
            token(
                client,
                REALM,
                {
                    "grant_type": "client_credentials",
                    "client_id": SA_CLIENT_ID,
                    "client_secret": "nope",
                },
            ),
        )
        save(
            "token_error_unknown_client.json",
            token(
                client,
                REALM,
                {"grant_type": "client_credentials", "client_id": "ghost", "client_secret": "x"},
            ),
        )
        save(
            "token_error_unknown_realm.json",
            token(
                client,
                "no-such-realm",
                {
                    "grant_type": "client_credentials",
                    "client_id": SA_CLIENT_ID,
                    "client_secret": SA_CLIENT_SECRET,
                },
            ),
        )
        save(
            "token_error_public_client.json",
            token(
                client,
                REALM,
                {"grant_type": "client_credentials", "client_id": "demo-app"},
            ),
        )

        # ---- reads with the service-account token ---------------------------
        rp = f"{adm}/{REALM}"
        save("serverinfo_sa.json", client.get(f"{BASE}/admin/serverinfo", headers=sa))
        save("serverinfo_admin.json", client.get(f"{BASE}/admin/serverinfo", headers=admin))
        save("realm.json", client.get(rp, headers=sa))
        save("users_count.json", client.get(f"{rp}/users/count", headers=sa))
        save(
            "clients_brief.json",
            client.get(f"{rp}/clients", params={"briefRepresentation": "true"}, headers=sa),
        )
        save("client_session_stats.json", client.get(f"{rp}/client-session-stats", headers=sa))
        save("events_config.json", client.get(f"{rp}/events/config", headers=sa))
        save("events_all.json", client.get(f"{rp}/events", params={"max": 100}, headers=sa))
        save(
            "events_login_error.json",
            client.get(f"{rp}/events", params={"type": "LOGIN_ERROR", "max": 100}, headers=sa),
        )
        save(
            "events_login_error_paged.json",
            client.get(
                f"{rp}/events",
                params={"type": "LOGIN_ERROR", "first": 2, "max": 2},
                headers=sa,
            ),
        )
        save(
            "events_for_carol.json",
            client.get(f"{rp}/events", params={"user": ids["carol"], "max": 20}, headers=sa),
        )
        save(
            "events_bad_type.json",
            client.get(f"{rp}/events", params={"type": "NOT_A_TYPE", "max": 5}, headers=sa),
        )
        save("admin_events.json", client.get(f"{rp}/admin-events", params={"max": 100}, headers=sa))
        save(
            "admin_events_paged.json",
            client.get(f"{rp}/admin-events", params={"first": 0, "max": 3}, headers=sa),
        )
        save(
            "user_search_alice.json",
            client.get(f"{rp}/users", params={"username": "alice", "exact": "true"}, headers=sa),
        )
        save(
            "user_search_by_email.json",
            client.get(
                f"{rp}/users", params={"email": "carol@example.com", "exact": "true"}, headers=sa
            ),
        )
        save(
            "user_search_nobody.json",
            client.get(f"{rp}/users", params={"username": "nobody", "exact": "true"}, headers=sa),
        )
        save(
            "user_search_prefix.json",
            client.get(f"{rp}/users", params={"search": "a", "max": 10}, headers=sa),
        )
        save("user_get_dave.json", client.get(f"{rp}/users/{ids['dave']}", headers=sa))
        save(
            "user_get_bogus_id.json",
            client.get(f"{rp}/users/00000000-0000-0000-0000-000000000000", headers=sa),
        )
        save(
            "brute_force_carol.json",
            client.get(
                f"{rp}/attack-detection/brute-force/users/{ids['carol']}", headers=sa
            ),
        )
        save(
            "brute_force_alice.json",
            client.get(
                f"{rp}/attack-detection/brute-force/users/{ids['alice']}", headers=sa
            ),
        )
        save("user_sessions_alice.json", client.get(f"{rp}/users/{ids['alice']}/sessions", headers=sa))
        save("user_sessions_carol.json", client.get(f"{rp}/users/{ids['carol']}/sessions", headers=sa))
        save(
            "user_offline_sessions_alice.json",
            client.get(
                f"{rp}/users/{ids['alice']}/offline-sessions/{sa_client_id}", headers=sa
            ),
        )
        save(
            "user_role_mappings_alice.json",
            client.get(f"{rp}/users/{ids['alice']}/role-mappings/realm", headers=sa),
        )
        save(
            "user_credentials_alice.json",
            client.get(f"{rp}/users/{ids['alice']}/credentials", headers=sa),
        )
        save(
            "user_federated_identity_alice.json",
            client.get(f"{rp}/users/{ids['alice']}/federated-identity", headers=sa),
        )
        save("realm_forbidden_master.json", client.get(f"{adm}/master", headers=sa))
        save("realm_not_found.json", client.get(f"{adm}/no-such-realm", headers=sa))
        save(
            "realm_unauthorized.json",
            client.get(rp, headers={"Authorization": "Bearer not-a-token"}),
        )
        save("realm_no_token.json", client.get(rp))
        # A realm whose events are off (master by default) -- admin token,
        # shows what "events disabled" looks like on the read side.
        save("master_events_config.json", client.get(f"{adm}/master/events/config", headers=admin))
        save("master_events.json", client.get(f"{adm}/master/events", params={"max": 5}, headers=admin))
        save(
            "master_admin_events.json",
            client.get(f"{adm}/master/admin-events", params={"max": 5}, headers=admin),
        )
        # Role checks: an SA with fewer roles -> what does 403 look like?
        save(
            "sa_write_forbidden.json",
            client.put(rp, headers=sa, json={"displayName": "should be forbidden"}),
        )

        # ---- management port -------------------------------------------------
        for path in ("/health", "/health/ready", "/health/live", "/health/started"):
            save("mgmt" + path.replace("/", "_") + ".json", client.get(f"{MGMT}{path}"))
        save("mgmt_metrics.txt", client.get(f"{MGMT}/metrics"))
        save("mgmt_health_on_main_port.json", client.get(f"{BASE}/health"))
        save("root_main_port.txt", client.get(f"{BASE}/"))
        save("openid_configuration.json", client.get(f"{BASE}/realms/{REALM}/.well-known/openid-configuration"))
        save("openid_configuration_missing_realm.json", client.get(f"{BASE}/realms/no-such-realm/.well-known/openid-configuration"))

        (OUT / "index.json").write_text(json.dumps(INDEX, indent=2) + "\n")
        (OUT / "ids.json").write_text(
            json.dumps(
                {"users": ids, "sa_client": sa_client_id, "sa_user": sa_user["id"], "realm_management_client": rm["id"], "sa_token_keys": sorted(sa_tok.keys()) if isinstance(sa_tok, dict) else None},
                indent=2,
            )
            + "\n"
        )
        print("done ->", OUT)


if __name__ == "__main__":
    main()

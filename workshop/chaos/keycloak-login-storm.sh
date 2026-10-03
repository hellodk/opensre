#!/usr/bin/env bash
# Act 3 fault: bad-login storm against Keycloak to exercise login-failure triage.
# Read-only against real accounts: wrong password, so nothing is changed.
set -euo pipefail

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak-keycloakx-http.utilities.svc.cluster.local}"
REALM="${REALM:-master}"
CLIENT="${CLIENT:-admin-cli}"
USERNAME="${USERNAME:-workshop-chaos-probe}"
COUNT="${COUNT:-50}"
TOKEN_URL="$KEYCLOAK_URL/realms/$REALM/protocol/openid-connect/token"

echo "REVERT: stateless — failed logins age out; unlock any locked probe user in the Keycloak admin console"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: $COUNT POSTs to $TOKEN_URL (wrong password, user $USERNAME)"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

for _ in $(seq 1 "$COUNT"); do
    curl -s -o /dev/null -w "%{http_code}\n" \
        -d "client_id=$CLIENT" -d "username=$USERNAME" \
        -d "password=wrong-password-chaos" -d "grant_type=password" \
        "$TOKEN_URL"
done | sort | uniq -c

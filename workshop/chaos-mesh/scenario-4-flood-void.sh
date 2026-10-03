#!/usr/bin/env bash
# Scenario 4 — publish into the void.
# Non-stop publishing to a subject nobody subscribes to. Core NATS silently
# drops undeliverable messages, so the publisher connection accrues in_msgs
# while out_msgs stays at zero and nothing downstream ever sees them —
# a silent black hole that masquerades as normal load.
# Detect: get_nats_connections -> pub conn in_msgs climbs, out_msgs 0;
#         get_nats_subscriptions -> zero interest on the subject.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hetu}"
BOX="${BOX:-deploy/hetu-nats-box}"
NATS_URL="${NATS_URL:-hetu-nats.hetu.svc.cluster.local:4222}"
VOID_SUBJECT="${VOID_SUBJECT:-_test.void}"
VOID_COUNT="${VOID_COUNT:-100000}"

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

echo "REVERT: nothing persists — the messages die on the wire; purge nothing."

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl exec $BOX -- nats pub -s $NATS_URL $VOID_SUBJECT --count $VOID_COUNT chaos-payload"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl -n "$NAMESPACE" exec "$BOX" -- nats pub -s "$NATS_URL" "$VOID_SUBJECT" --count "$VOID_COUNT" chaos-payload
echo "Published $VOID_COUNT to $VOID_SUBJECT with zero subscribers; observe in_msgs vs out_msgs."
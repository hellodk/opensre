#!/usr/bin/env bash
# Scenario 3 — duplicate subscriptions.
# Three independent subscribers attach to the same subject with no queue
# group, so NATS fans every message out to all (in healthy core NATS the
# contract is one copy per non-queue subscription). Worth a line about
# delivery amplification: 30 publishes become 90 deliveries.
# Detect: get_nats_subscriptions -> multiple live interests on one subject;
#         get_nats_connections -> three conns each with out_msgs == COUNT.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hetu}"
BOX="${BOX:-deploy/hetu-nats-box}"
NATS_URL="${NATS_URL:-hetu-nats.hetu.svc.cluster.local:4222}"
SUBJECT="${SUBJECT:-orders.events}"
DUP_COUNT="${DUP_COUNT:-3}"
PUBLISH_COUNT="${PUBLISH_COUNT:-30}"

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

echo "REVERT: kubectl -n $NAMESPACE exec $BOX -- pkill -f 'nats sub'; kill $(jobs -p) 2>/dev/null"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: start $DUP_COUNT subscribers on $BOX to $NATS_URL (subject $SUBJECT)"
    echo "[dry-run] would: publish $PUBLISH_COUNT messages -> observed delivery x$DUP_COUNT"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

PIDS=()
for _ in $(seq 1 "$DUP_COUNT"); do
    kubectl -n "$NAMESPACE" exec "$BOX" -- nats sub -s "$NATS_URL" "$SUBJECT" --count=2147483647 >/dev/null 2>&1 &
    PIDS+=("$!")
done
sleep 3
kubectl -n "$NAMESPACE" exec "$BOX" -- nats pub -s "$NATS_URL" "$SUBJECT" --count "$PUBLISH_COUNT" chaos-payload
echo "Published $PUBLISH_COUNT to $SUBJECT with $DUP_COUNT live subscriptions; observe out_msgs amplification."
wait "${PIDS[@]}" 2>/dev/null || true
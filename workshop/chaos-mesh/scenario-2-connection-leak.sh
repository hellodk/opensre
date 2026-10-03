#!/usr/bin/env bash
# Scenario 2 — connection leak.
# A naive loop opens NATS connections and never closes them (a real client
# bug pattern). Each background `nats sub` holds one connection open.
# Detect: get_nats_connections -> num_connections climbs past a sane
#         baseline while no workload justifies the growth.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hetu}"
BOX="${BOX:-deploy/hetu-nats-box}"
NATS_URL="${NATS_URL:-hetu-nats.hetu.svc.cluster.local:4222}"
LEAK_SUBJECT_PREFIX="${LEAK_SUBJECT_PREFIX:-_test.leak}"
LEAK_COUNT="${LEAK_COUNT:-40}"

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

echo "REVERT: kubectl -n $NAMESPACE exec $BOX -- pkill -f 'nats sub'; kill $(jobs -p) 2>/dev/null"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: spawn $LEAK_COUNT background subscribers on $BOX to $NATS_URL (subjects $LEAK_SUBJECT_PREFIX.*)"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

PIDS=()
for i in $(seq 1 "$LEAK_COUNT"); do
    kubectl -n "$NAMESPACE" exec "$BOX" -- nats sub -s "$NATS_URL" "$LEAK_SUBJECT_PREFIX.$i" --count=2147483647 >/dev/null 2>&1 &
    PIDS+=("$!")
done
echo "Opened $LEAK_COUNT connections; observe get_nats_connections over the next minute. Ctrl-C to stop leaking (see REVERT above)."
wait "${PIDS[@]}" 2>/dev/null || true
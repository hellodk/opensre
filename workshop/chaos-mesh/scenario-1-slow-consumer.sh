#!/usr/bin/env bash
# Scenario 1 — slow consumer.
# A subscriber that cannot keep up: we park a live `nats sub` on nats-box,
# sieve its inbound with a NetworkChaos delay, then burst 20k messages so
# NATS builds outbound backpressure against the stalled socket.
# Detect: get_nats_connections -> slow_consumer flag / pending_bytes climb
#         while in_msgs runs far ahead of out_msgs on the subscriber conn.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hetu}"
BOX="${BOX:-deploy/hetu-nats-box}"
NATS_URL="${NATS_URL:-hetu-nats.hetu.svc.cluster.local:4222}"
SUBJECT="${SUBJECT:-orders.events}"
COUNT="${COUNT:-20000}"

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

CHAOS_NAME="nats-slow-consumer"

echo "REVERT: kubectl delete networkchaos $CHAOS_NAME -n $NAMESPACE; kubectl -n $NAMESPACE exec $BOX -- pkill -f 'nats sub'"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: start slow subscriber on $BOX to $NATS_URL (subject $SUBJECT)"
    echo "[dry-run] would: apply NetworkChaos $CHAOS_NAME inbound-delay 800ms on nats-box"
    echo "[dry-run] would: kubectl exec $BOX -- nats pub -s $NATS_URL $SUBJECT --count $COUNT chaos-payload"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl -n "$NAMESPACE" exec "$BOX" -- nats sub -s "$NATS_URL" "$SUBJECT" --count=2147483647 &
SUB_PID=$!
sleep 2
kubectl apply -f - <<YAML
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: $CHAOS_NAME
  namespace: $NAMESPACE
  labels:
    workshop: chaos-mesh
spec:
  action: delay
  mode: one
  selector:
    namespaces:
      - $NAMESPACE
    labelSelectors:
      app.kubernetes.io/name: nats
      app.kubernetes.io/component: nats-box
  delay:
    latency: "800ms"
    jitter: "200ms"
    correlation: "100"
  duration: "90s"
YAML
sleep 2
kubectl -n "$NAMESPACE" exec "$BOX" -- nats pub -s "$NATS_URL" "$SUBJECT" --count "$COUNT" chaos-payload
echo "Observe now (slow-consumer window ~90s), then: kill $SUB_PID; kubectl delete networkchaos $CHAOS_NAME -n $NAMESPACE"
wait "$SUB_PID" 2>/dev/null || true
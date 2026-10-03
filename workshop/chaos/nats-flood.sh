#!/usr/bin/env bash
# Act 2 fault: flood a NATS subject to build consumer lag.
# Publishes from the existing hetu-nats-box pod; no new workloads.
set -euo pipefail

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

NATS_SVC="${NATS_SVC:-hetu-nats.hetu.svc.cluster.local:4222}"
SUBJECT="${SUBJECT:-orders.events}"
COUNT="${COUNT:-5000}"

echo "REVERT: lag drains on its own; to purge test traffic: kubectl -n hetu exec deploy/hetu-nats-box -- nats stream purge <stream> --force"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl -n hetu exec deploy/hetu-nats-box -- nats pub -s $NATS_SVC $SUBJECT --count $COUNT chaos-payload"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl -n hetu exec deploy/hetu-nats-box -- nats pub -s "$NATS_SVC" "$SUBJECT" --count "$COUNT" chaos-payload

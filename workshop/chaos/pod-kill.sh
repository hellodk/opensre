#!/usr/bin/env bash
# Act 1 fault: kill one store-payments pod to trigger a restarts/error spike.
# Safe: the Deployment keeps the second replica up and recreates the pod.
set -euo pipefail

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

NAMESPACE="${NAMESPACE:-prod}"
SELECTOR="${SELECTOR:-app=store,service=payments}"

echo "REVERT: automatic — kubectl -n $NAMESPACE get pods -l $SELECTOR (replica is recreated); abort: ./abort-all.sh --confirm"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl -n $NAMESPACE delete pod -l $SELECTOR --grace-period=0 (one pod)"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl -n "$NAMESPACE" delete pod -l "$SELECTOR" --grace-period=0
kubectl -n "$NAMESPACE" get pods -l "$SELECTOR"

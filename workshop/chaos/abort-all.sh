#!/usr/bin/env bash
# Global revert: restores prod deploys to 2 replicas and clears chaos-load workloads.
# Idempotent — safe to run twice.
set -euo pipefail

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

echo "REVERT: not applicable — this IS the revert (re-run is safe)"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: rescale 4 prod deploys to 2 replicas and delete chaos-load pods/jobs"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl scale -n prod deploy/store-api-gateway --replicas=2
kubectl scale -n prod deploy/store-inventory --replicas=2
kubectl scale -n prod deploy/store-orders --replicas=2
kubectl scale -n prod deploy/store-payments --replicas=2
kubectl -n chaos-load delete pods --all --ignore-not-found --wait=false
kubectl -n chaos-load delete jobs --all --ignore-not-found --wait=false
kubectl -n prod get pods -l app=store

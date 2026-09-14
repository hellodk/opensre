#!/usr/bin/env bash
# Act 2 fault: scale a prod deploy to zero to produce 503s at the edge.
# Default target is store-orders. Pass --revert to scale back to 2 replicas.
set -euo pipefail

CONFIRM=0
REVERT=0
DEPLOY="${DEPLOY:-store-orders}"
NAMESPACE="${NAMESPACE:-prod}"
for arg in "$@"; do
    case "$arg" in
        --confirm) CONFIRM=1 ;;
        --revert) REVERT=1 ;;
        --deploy=*) DEPLOY="${arg#--deploy=}" ;;
        *) echo "unknown arg: $arg (want --confirm, --revert, --deploy=NAME)" >&2; exit 2 ;;
    esac
done

if [[ "$REVERT" == "1" ]]; then
    echo "REVERT: kubectl scale -n $NAMESPACE deploy/$DEPLOY --replicas=2 (this IS the revert)"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "[dry-run] would: kubectl scale -n $NAMESPACE deploy/$DEPLOY --replicas=2"
        exit 0
    fi
    if [[ "$CONFIRM" != "1" ]]; then
        echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
        exit 2
    fi
    kubectl scale -n "$NAMESPACE" "deploy/$DEPLOY" --replicas=2
    exit 0
fi

echo "REVERT: ./scale-to-zero.sh --revert --deploy=$DEPLOY --confirm (or ./abort-all.sh --confirm)"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl scale -n $NAMESPACE deploy/$DEPLOY --replicas=0"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl scale -n "$NAMESPACE" "deploy/$DEPLOY" --replicas=0

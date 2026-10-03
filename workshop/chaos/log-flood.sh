#!/usr/bin/env bash
# Act 1 support fault: short-lived log emitter in chaos-load to spike victoria-logs.
# Self-contained; abort-all.sh removes it.
set -euo pipefail

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

NAMESPACE="${NAMESPACE:-chaos-load}"
POD="${POD:-log-flooder}"
LINES="${LINES:-2000}"

echo "REVERT: kubectl -n $NAMESPACE delete pod $POD --ignore-not-found (or ./abort-all.sh --confirm)"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl -n $NAMESPACE run $POD --image=busybox:1.36.1 --restart=Never -- emit $LINES log lines, then delete it"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl -n "$NAMESPACE" run "$POD" --image=busybox:1.36.1 --restart=Never -- \
    /bin/sh -c "i=1; while [ \$i -le $LINES ]; do echo \"chaos-log line \$i service=store-payments level=error\"; i=\$((i+1)); done; sleep 30"
kubectl -n "$NAMESPACE" delete pod "$POD" --ignore-not-found --wait=false

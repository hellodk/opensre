#!/usr/bin/env bash
# Revert every Chaos Mesh experiment the kit can create plus any leaked
# subscriber connections parked on nats-box. Targets by label only, so it
# never touches experiments applied by hand without the kit's labels.
set -euo pipefail

NAMESPACE="${NAMESPACE:-hetu}"
BOX="${BOX:-deploy/hetu-nats-box}"

CONFIRM=0
if [[ "${1:-}" == "--confirm" ]]; then CONFIRM=1; fi

echo "REVERT (this script): deletes labeled chaos experiments + leaked subscribers."

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "[dry-run] would: kubectl delete networkchaos,stresschaos,podchaos -n $NAMESPACE -l workshop=chaos-mesh"
    echo "[dry-run] would: kubectl exec $BOX -- pkill -f 'nats sub' || true"
    exit 0
fi
if [[ "$CONFIRM" != "1" ]]; then
    echo "Refusing live run without --confirm (re-run with --confirm, or DRY_RUN=1 to preview)." >&2
    exit 2
fi

kubectl delete networkchaos,stresschaos,podchaos -n "$NAMESPACE" -l workshop=chaos-mesh || true
kubectl -n "$NAMESPACE" exec "$BOX" -- pkill -f 'nats sub' 2>/dev/null || true
echo "Cleaned workshop chaos experiments in $NAMESPACE."
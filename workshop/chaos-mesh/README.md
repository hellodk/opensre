# Chaos Mesh kit — 8 NATS trouble scenarios

Eight injectable faults against the workshop's `hetu` NATS (core NATS 2.11,
**JetStream disabled**). Four are pure Chaos Mesh experiments (YAML applied
with `kubectl apply -f`); four are client-behaviour drivers that run the
already-deployed `nats-box` pod — Chaos Mesh cannot fabricate connections or
subscriptions, that's client code, so the driver scripts do it.

Every experiment carries the label `workshop: chaos-mesh` so `revert-all.sh`
removes nothing that was not applied by this kit.

## Prerequisites

- NATS monitoring reachable from the machine running OpenSRE:
  `kubectl port-forward -n hetu svc/hetu-nats-headless 8222:8222`
- OpenSRE NATS integration configured with that URL (`NATS_MONITOR_URL`), so
  `opensre health` shows `nats` available.

## Observation

Each scenario lists the OpenSRE tools that surface the fault. Run, e.g.,

```
opensre ask --allowed-tool get_nats_connections "is NATS healthy?"
```

Whatever the tool replies, only record what you actually observe — the point
of each fault is that the NATS process itself looks fine while the *delivery
contract* is broken.

## Scenario matrix

| # | Fault | Mechanism | Observe with | Revert |
|---|-------|-----------|--------------|--------|
| 1 | Slow consumer | nats-box subscriber + NetworkChaos delay on its inbound, then 20k-burst publish | `get_nats_connections` → `slow_consumer` flag / `pending_bytes` / in_msgs ≫ out_msgs | delete `nats-slow-consumer`; pkill the subscriber |
| 2 | Connection leak | 40 background `nats sub` connections left open | `get_nats_connections` → `num_connections` climbs, no workload justifies it | `revert-all.sh` |
| 3 | Duplicate subscriptions | 3 live `nats sub` on one subject, no queue group; 30 publishes fan out ×3 | `get_nats_subscriptions` (3 interests), `get_nats_connections` (out_msgs ×3) | `revert-all.sh` |
| 4 | Publish into the void | 100k publishes to a subject nobody subscribes to | `get_nats_connections` → in_msgs climbs, out_msgs stays 0 | none (messages die on the wire) |
| 5 | Server latency | NetworkChaos delay 500ms inbound on the NATS pod | `get_nats_server_status` + `get_nats_connections` → server "ok", latency inflated off-process | `kubectl delete -f scenario-5-nats-latency.yaml` |
| 6 | Pod killed | PodChaos kill (grace 0) on the NATS pod | `get_nats_connections` → drops to 0 then recovers; reconnect storm | `kubectl delete -f scenario-6-nats-pod-kill.yaml` |
| 7 | CPU starvation | StressChaos (1 worker, 80% load) on the NATS pod | `get_nats_server_status` → reachable but saturated; latency/keepalives degrade | `kubectl delete -f scenario-7-nats-cpu.yaml` |
| 8 | Consumers cut off | NetworkChaos partition NATS ↔ nats-box (both directions) | `get_nats_connections` + `get_nats_subscriptions` → count collapses, server stays "up" | `kubectl delete -f scenario-8-partition.yaml` |

## Running a scenario

Client-behaviour drivers (`scenario-1` … `scenario-4`)): gate like the rest of
the kit — `DRY_RUN=1 ./scenario-N-….sh` to preview, then
`./scenario-N-….sh --confirm`. They print their exact revert command first.

Chaos Mesh experiments (`scenario-5` … `scenario-8`): apply and delete with
kubectl. Duration is built into each manifest (60s–2m).

## Hard limits

- JetStream is off, so there are no streams/consumers — scenario 4 is core
  NATS drop semantics, not a lag pile.
- The kit only touches pods it owns through labels; it never kills or edits
  the OpenSRE collector/analyzer or the `monitoring` stack.
- `revert-all.sh` needs `--confirm` like every script here.
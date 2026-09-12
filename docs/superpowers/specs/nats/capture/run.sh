#!/usr/bin/env bash
# Bring up a throwaway three-node nats-server JetStream cluster plus a
# standalone non-JetStream server, then provision streams, consumers and
# backlog with nats-box. Monitoring ports are bound to 127.0.0.1 only.
#
#   n1  client 127.0.0.1:14222  monitor 127.0.0.1:18222
#   n2  client 127.0.0.1:14223  monitor 127.0.0.1:18223
#   n3  client 127.0.0.1:14224  monitor 127.0.0.1:18224
#   solo (no JetStream, no cluster)   monitor 127.0.0.1:18225
#
# Tear down:  docker rm -f opensre-nats-n1 opensre-nats-n2 opensre-nats-n3 \
#               opensre-nats-solo opensre-nats-pub; docker network rm opensre-nats
set -euo pipefail

NATS_IMAGE="nats:2.14.6-alpine"
BOX_IMAGE="natsio/nats-box:0.19.7"
NET="opensre-nats"

docker rm -f opensre-nats-n1 opensre-nats-n2 opensre-nats-n3 opensre-nats-solo opensre-nats-pub >/dev/null 2>&1 || true
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

routes="nats://opensre-nats-n1:6222,nats://opensre-nats-n2:6222,nats://opensre-nats-n3:6222"
i=0
for n in n1 n2 n3; do
  docker run -d --name "opensre-nats-$n" --network "$NET" \
    -p "127.0.0.1:$((14222 + i)):4222" -p "127.0.0.1:$((18222 + i)):8222" \
    "$NATS_IMAGE" \
    --name "$n" --server_name "$n" \
    --js --store_dir /tmp/js \
    --cluster_name opensre-demo --cluster "nats://0.0.0.0:6222" --routes "$routes" \
    -m 8222 >/dev/null
  i=$((i + 1))
done

docker run -d --name opensre-nats-solo --network "$NET" \
  -p 127.0.0.1:18225:8222 \
  "$NATS_IMAGE" --server_name solo -m 8222 >/dev/null

echo "waiting for cluster meta leader"
for _ in $(seq 1 30); do
  if curl -fs http://127.0.0.1:18222/jsz | grep -q '"leader"'; then break; fi
  sleep 1
done

box() { docker run --rm --network "$NET" "$BOX_IMAGE" nats --server nats://opensre-nats-n1:4222 "$@"; }

# Streams: one replicated with limits, one small single-replica, one work-queue.
box stream add ORDERS --subjects "orders.>" --storage file --replicas 3 \
  --retention limits --max-msgs 10000 --max-bytes 10485760 --max-age 24h \
  --discard old --dupe-window 2m --max-msg-size=-1 --max-msgs-per-subject=-1 \
  --no-allow-rollup --no-deny-delete --no-deny-purge --defaults >/dev/null
box stream add EVENTS --subjects "events.>" --storage memory --replicas 1 \
  --retention limits --max-msgs 500 --max-bytes=-1 --max-age 1h \
  --discard new --dupe-window 2m --max-msg-size=-1 --max-msgs-per-subject=-1 \
  --no-allow-rollup --no-deny-delete --no-deny-purge --defaults >/dev/null
box stream add JOBS --subjects "jobs.>" --storage file --replicas 3 \
  --retention work --max-msgs=-1 --max-bytes=-1 --max-age=-1 \
  --discard old --dupe-window 2m --max-msg-size=-1 --max-msgs-per-subject=-1 \
  --no-allow-rollup --no-deny-delete --no-deny-purge --defaults >/dev/null

# Consumers: durable pull with backlog, durable push with ack pending, one never consumed.
box consumer add ORDERS billing --pull --deliver all --ack explicit --max-deliver 5 \
  --max-pending 1000 --replay instant --filter "orders.created" --defaults >/dev/null
box consumer add ORDERS audit --pull --deliver all --ack none --max-deliver=-1 \
  --max-pending 0 --replay instant --defaults >/dev/null
box consumer add JOBS worker --pull --deliver all --ack explicit --max-deliver 3 \
  --max-pending 100 --replay instant --defaults >/dev/null
box consumer add EVENTS stale --pull --deliver all --ack explicit --max-deliver=-1 \
  --max-pending 0 --replay instant --defaults >/dev/null

# Backlog: 600 orders (400 created / 200 shipped), 40 events, 25 jobs.
box pub orders.created --count 400 "order {{Count}}" >/dev/null
box pub orders.shipped --count 200 "shipped {{Count}}" >/dev/null
box pub events.login   --count 40  "login {{Count}}" >/dev/null
box pub jobs.render    --count 25  "job {{Count}}" >/dev/null

# Consume partially: billing takes 50 and acks, then 20 without ack (ack pending);
# worker takes 5 and nak's them to create redeliveries.
box consumer next ORDERS billing --count 50 --ack --raw >/dev/null 2>&1 || true
box consumer next ORDERS billing --count 20 --no-ack --raw >/dev/null 2>&1 || true
box consumer next JOBS worker --count 5 --nak --raw >/dev/null 2>&1 || true

# Long-lived core NATS subscriber and a chatty publisher so /connz and /subsz have data.
docker run -d --name opensre-nats-pub --network "$NET" "$BOX_IMAGE" \
  sh -c 'nats --server nats://opensre-nats-n2:4222 sub "telemetry.>" --queue tail >/dev/null 2>&1 & \
         nats --server nats://opensre-nats-n1:4222 sub "telemetry.>" >/dev/null 2>&1 & \
         while true; do nats --server nats://opensre-nats-n3:4222 pub telemetry.cpu --count 200 "{{Count}}" >/dev/null 2>&1; sleep 1; done' >/dev/null

sleep 3
echo "cluster ready; monitoring at http://127.0.0.1:18222 (n1) 18223 (n2) 18224 (n3), solo at 18225"

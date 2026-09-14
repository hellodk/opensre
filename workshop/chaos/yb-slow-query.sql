-- Act 3 fault: read-only slow-query probe for YugabyteDB (port 5433).
-- Run via: kubectl -n databases exec yb-tserver-0 -- ysqlsh -h localhost -U yugabyte -f /tmp/yb-slow-query.sql
-- or port-forward 5433 and run from the workshop machine. Read-only by
-- construction: only SELECT + pg_sleep, no writes, no DDL.

SELECT pg_sleep(2), count(*) FROM pg_stat_activity;

SELECT datname, usename, state, now() - query_start AS running_for, left(query, 120)
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY query_start
LIMIT 20;

SELECT schemaname, relname, seq_scan, idx_scan, n_live_tup
FROM pg_stat_user_tables
ORDER BY seq_scan DESC
LIMIT 20;

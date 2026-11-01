# Operations

Targets (NFR-01, NFR-06): 99.9% availability, RTO 4 h or less, RPO 15 min or less, encrypted and immutable backups, and a restore tested every quarter.

## Availability

- The backend runs 3 or more replicas behind an HPA (CPU 70%), and the frontend runs 2. Both spread across nodes and have PodDisruptionBudgets that allow one pod down at a time.
- PostgreSQL runs as 3 instances with a synchronous standby. CloudNativePG promotes a standby automatically if the primary fails, usually within a minute, without losing committed data.
- During a declared major incident, raise the floor so scaling never lags the load: `helm upgrade ... --reuse-values --set backend.replicas=6`.
- `/healthz` (process alive) and `/readyz` (database reachable) drive liveness and readiness. Alert on Gateway 5xx rate and p95 latency, CloudNativePG replication lag, and failed CronJobs.

## Backups

| What | How | Meets |
|---|---|---|
| WAL | Archived continuously by the Barman Cloud plugin, a segment at least every 5 min (`archive_timeout`) | RPO 15 min |
| Base backups | ScheduledBackup daily at 02:00 UTC, plus one at install | RTO |
| Encryption | SSE-KMS on every object (`backup.encryption`), TLS in transit | SR-04 |
| Immutability | Object Lock in compliance mode on the backup bucket | NFR-06 |
| Retention | `backup.retention` (default 30 days) | |
| Evidence objects | S3 versioning and cross-region replication on the evidence bucket | |

Check health:

```sh
kubectl -n casemgr get backups
kubectl -n casemgr get cluster casemgr-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")]}'
```

## Restore

Restores always go into a new namespace (or cluster), never over the running database.

1. Create the namespace with Pod Security `restricted`, and provision the same secrets (section 3 of `docs/deployment.md`).
2. Install the database chart in recovery mode. Give the restored cluster a new `backup.serverName` so its WAL never overwrites the source's backups:

   ```sh
   helm install casemgr-db deploy/helm/casemgr-db -n casemgr-restore -f db-values.yaml \
     --set recovery.enabled=true \
     --set recovery.sourceServerName=casemgr-db \
     --set backup.serverName=casemgr-db-restore-$(date +%Y%m%d) \
     --set recovery.targetTime=2026-11-01T14:30:00Z   # omit to replay all archived WAL
   kubectl -n casemgr-restore wait cluster/casemgr-db --for=condition=Ready --timeout=4h
   ```

3. Install the app chart with the same values: `helm install casemgr deploy/helm/casemgr -n casemgr-restore -f values.yaml --wait`.
4. Verify: `kubectl -n casemgr-restore exec deploy/casemgr-backend -- python -m app.jobs audit_verify` reports `"intact": true`, case counts match expectations, and a test user can sign in and open a recent case.
5. To cut over, point the Gateway hostname (DNS or `gateway.hostname`) at the restored release, then retire the old namespace.

Evidence objects are not part of the database backup. They stay in the evidence bucket, which the restored app uses as is.

## Restore drill (quarterly)

`deploy/kind/restore-drill.sh` runs the restore steps against the local kind cluster: it forces a WAL switch, waits for it to be archived, restores into `casemgr-restore`, deploys the app, verifies the audit chain, and compares row counts with the source. CI runs it on every pull request. In production, run the same steps against the latest backups every quarter, and record the date, elapsed time, and result here.

| Date | Environment | Data | Restore time | Result |
|---|---|---|---|---|
| 2026-11-01 | kind, 4 CPUs | 36 cases, 513 audit entries | 93 s (database and app) | Pass: counts match, audit chain intact |

## Key rotation

| Key | Rotation |
|---|---|
| `CASEMGR_SECRET_KEY` | Update the secret and restart the backend (`kubectl rollout restart deploy/casemgr-backend`). It only signs the short-lived login state cookie, so only logins in progress are affected |
| OIDC client secret | Create a second secret in the IdP, update `casemgr`, restart, then revoke the old one |
| `casemgr_app` password | Update `casemgr-db-runtime`. CloudNativePG applies the new password to the role, then restart the backend |
| Database CA and server certificates | Managed and renewed by CloudNativePG |
| Gateway certificate | Renewed by cert-manager |
| KMS keys (storage, buckets) | Enable automatic yearly rotation in the KMS |
| API tokens | Expire by design. Revoke and reissue in Settings |

## Background jobs

| Job | Default schedule | Purpose |
|---|---|---|
| `sla` | every minute | SLA escalations and notification delivery |
| `clocks` | every 5 min | Regulatory deadline warnings and breaches |
| `evidence_hash` | every minute | Hash new evidence uploads |
| `email_intake` | every minute | Turn intake mail into cases |
| `retention` | daily | Purge closed cases past retention |
| `audit_verify` | daily | Check the audit log's hash chain. Exits non-zero, and so fails the Job, if the chain is broken |

Run a job now: `kubectl -n casemgr create job --from=cronjob/casemgr-audit-verify audit-verify-now`.

## Load testing (NFR-02)

`loadtest/seed.sql` adds synthetic cases and observables to the `detect-respond` tenant, plus a read-only API token. `loadtest/k6.js` then measures case views (p95 under 2 s) and search (p95 under 3 s) at a steady 20 case views and 5 searches per second. Run them only against a disposable environment:

```sh
psql "$OWNER_URL" -v cases=1000000 -v observables=100000000 -v token=$TOKEN -f loadtest/seed.sql
k6 run -e BASE_URL=https://casemgr.staging.example.com -e TOKEN=$TOKEN \
  -e FIRST_CASE=<first seeded number> -e CASES=1000000 -e OBSERVABLES=100000000 loadtest/k6.js
```

Search is bound by PostgreSQL CPU. Its cost grows with how many rows a query word matches, because every match is ranked. Size the database's CPU from a load test with realistic vocabulary.

Results:

| Date | Environment | Data | Case view p95 | Search p95 |
|---|---|---|---|---|
| 2026-11-01 | compose on one workstation: backend 1 CPU, PostgreSQL 2 CPUs | 1M cases, 10M observables | 886 ms | 2.88 s (PostgreSQL at its 2-CPU limit) |

The full-scale run (1M cases, 100M observables) belongs on staging hardware sized like production.

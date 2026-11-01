#!/usr/bin/env bash
# Restore drill on the local kind cluster: restore casemgr-db from its backups into a
# fresh namespace, deploy the app against it, and check that the data and the audit
# chain survived. Prints the elapsed time to compare with the 4 h RTO.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$PWD/.tools:$PATH" MSYS_NO_PATHCONV=1
NS=${1:-casemgr-restore}
COUNTS="select (select count(*) from cases) || ' cases, ' || (select count(*) from audit_log) || ' audit entries'"

psql() {
  local primary
  primary=$(kubectl -n "$1" get cluster casemgr-db -o jsonpath='{.status.currentPrimary}')
  kubectl -n "$1" exec "$primary" -c postgres -- psql -d casemgr -tAc "$2"
}

source_counts=$(psql casemgr "$COUNTS")
# Close the current WAL segment so the archive holds everything written so far.
segment=$(psql casemgr "select pg_walfile_name(pg_switch_wal())")
until [ "$(psql casemgr "select coalesce(last_archived_wal >= '$segment', false) from pg_stat_archiver")" = t ]; do
  sleep 5
done
start=$(date +%s)

kubectl create namespace "$NS"
kubectl label namespace "$NS" pod-security.kubernetes.io/enforce=restricted
for secret in casemgr casemgr-db-runtime casemgr-backup-s3 casemgr-tls; do
  kubectl -n casemgr get secret "$secret" -o yaml \
    | sed '/^  namespace:/d; /^  uid:/d; /^  resourceVersion:/d; /^  creationTimestamp:/d' \
    | kubectl -n "$NS" apply -f -
done

# The restored cluster archives to its own folder so it never overwrites the source's backups.
helm install casemgr-db deploy/helm/casemgr-db -n "$NS" -f deploy/kind/db-values.yaml \
  --set recovery.enabled=true --set backup.serverName="casemgr-db-restore-$start"
kubectl -n "$NS" wait cluster/casemgr-db --for=condition=Ready --timeout=60m
# The drill copy is reached only from inside the cluster, so its Gateway gets no node port.
helm install casemgr deploy/helm/casemgr -n "$NS" -f deploy/kind/values.yaml \
  --set gateway.envoyProxy= --set jobs=null --wait --timeout 15m

restored_counts=$(psql "$NS" "$COUNTS")
kubectl -n "$NS" exec deploy/casemgr-backend -- python -m app.jobs audit_verify
echo "source:   $source_counts"
echo "restored: $restored_counts"
echo "restore took $(( $(date +%s) - start )) s"
[ "$source_counts" = "$restored_counts" ]

#!/usr/bin/env bash
# Create a local kind cluster running casemgr from the Helm charts, with the same
# operators as production: Envoy Gateway, cert-manager, CloudNativePG, and Barman Cloud.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$PWD/.tools:$PATH" MSYS_NO_PATHCONV=1

kind create cluster --config deploy/kind/cluster.yaml
# Keep the node from using the whole machine.
docker update --cpus "${KIND_CPUS:-4}" casemgr-control-plane

for component in backend frontend; do
  docker build -t "casemgr-$component:kind" "$component"
  kind load docker-image --name casemgr "casemgr-$component:kind"
done

helm install eg oci://docker.io/envoyproxy/gateway-helm --version v1.9.3 \
  -n envoy-gateway-system --create-namespace --wait
helm install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.3 \
  -n cert-manager --create-namespace --set crds.enabled=true --set config.enableGatewayAPI=true --wait
kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.1.yaml
kubectl -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=5m
kubectl apply -f https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.15.1/manifest.yaml
kubectl -n cnpg-system rollout status deploy/barman-cloud --timeout=5m

kubectl create namespace casemgr-dev
kubectl -n casemgr-dev create configmap keycloak-realm --from-file=deploy/keycloak/casemgr-realm.json
kubectl apply -f deploy/kind/dev-services.yaml
kubectl -n casemgr-dev wait --for=condition=Available deploy --all --timeout=10m
kubectl -n casemgr-dev exec deploy/seaweedfs -- sh -c 'echo "s3.bucket.create -name casemgr-backups" | weed shell'

kubectl create namespace casemgr
kubectl label namespace casemgr pod-security.kubernetes.io/enforce=restricted
kubectl apply -f deploy/kind/platform.yaml
# Production provisions these through External Secrets (docs/deployment.md).
kubectl -n casemgr create secret generic casemgr \
  --from-literal=CASEMGR_OIDC_CLIENT_SECRET=dev-only-client-secret \
  --from-literal=CASEMGR_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=CASEMGR_S3_ACCESS_KEY=dev \
  --from-literal=CASEMGR_S3_SECRET_KEY=dev \
  --from-literal=CASEMGR_IMAP_URL='imap://intake%40example.test:intake@greenmail.casemgr-dev:3143/INBOX'
kubectl -n casemgr create secret generic casemgr-db-runtime --type=kubernetes.io/basic-auth \
  --from-literal=username=casemgr_app --from-literal=password="$(openssl rand -hex 24)"
kubectl -n casemgr create secret generic casemgr-backup-s3 \
  --from-literal=ACCESS_KEY_ID=dev --from-literal=ACCESS_SECRET_KEY=dev

helm install casemgr-db deploy/helm/casemgr-db -n casemgr -f deploy/kind/db-values.yaml
kubectl -n casemgr wait cluster/casemgr-db --for=condition=Ready --timeout=15m
helm install casemgr deploy/helm/casemgr -n casemgr -f deploy/kind/values.yaml --wait --timeout 10m

echo "casemgr is at https://localhost:8443 (self-signed certificate)"

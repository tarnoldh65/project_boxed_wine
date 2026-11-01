# Secure deployment

casemgr runs on Kubernetes from two Helm charts:

- `deploy/helm/casemgr-db`: PostgreSQL on CloudNativePG, with synchronous replication, continuous WAL archiving, and scheduled base backups.
- `deploy/helm/casemgr`: the API (backend), the web app (frontend), the Gateway and its routes, background CronJobs, and NetworkPolicies.

Both go in the same namespace. Install the database chart first. `deploy/kind/up.sh` runs every step below on a local kind cluster and serves as a working reference.

## Recommended platform

| Need | Recommendation |
|---|---|
| Kubernetes | EKS, AKS, or GKE. Self-hosted or air-gapped: RKE2 or OpenShift |
| Ingress | Gateway API with Envoy Gateway (TLS termination, rate limiting) |
| Certificates | cert-manager |
| PostgreSQL | CloudNativePG operator with the Barman Cloud plugin |
| Object storage | S3 with SSE-KMS and Object Lock; on-premises, Ceph RGW |
| Identity | Keycloak kept separate from corporate AD (so it still works if AD is compromised), or Entra ID / Okta |
| Secrets | External Secrets Operator backed by Vault or a cloud secret manager |
| GitOps | Argo CD |
| Admission policy | Kyverno: require signed images, block privileged pods |
| Observability | OpenTelemetry, Prometheus, Grafana, Loki; forward the audit log to the SIEM |

Versions tested: Kubernetes 1.37 (kind 0.33), Envoy Gateway 1.9.3, cert-manager 1.21.3, CloudNativePG 1.30.1, Barman Cloud plugin 0.15.1, Helm 4.3.

## 1. Cluster prerequisites

- At least three worker nodes across availability zones. The backend (three or more replicas) and the database (three instances) spread across nodes, and PodDisruptionBudgets keep a majority running during node drains.
- A CNI that enforces NetworkPolicy (Cilium, Calico, or the cloud provider's).
- metrics-server, so the backend's HorizontalPodAutoscaler can scale on CPU.
- A StorageClass with encryption at rest under a customer-managed key (for example EBS gp3 with KMS, Azure Disk with a disk encryption set, or GCE PD with CMEK).
- Operators: Envoy Gateway, cert-manager (with `config.enableGatewayAPI=true`), CloudNativePG, and the Barman Cloud plugin. See `deploy/kind/up.sh` for the install commands.
- A namespace that enforces Pod Security `restricted`. Every pod in both charts runs as non-root with a read-only root filesystem, no privilege escalation, all capabilities dropped, and the RuntimeDefault seccomp profile.

```sh
kubectl create namespace casemgr
kubectl label namespace casemgr pod-security.kubernetes.io/enforce=restricted
```

## 2. Identity provider

casemgr signs users in only through OIDC (authorization code flow with PKCE).

1. Create a confidential client `casemgr` with the redirect URI `https://<host>/api/auth/callback` and the post-logout redirect URI `https://<host>`.
2. Add a `groups` claim to the ID token and userinfo, containing group names (not paths).
3. Create groups named `casemgr-<tenant>-<role>`, for example `casemgr-detect-respond-analyst`. The roles are `admin`, `ir_lead`, `analyst`, `legal`, `executive`, and `auditor`. The flags `restricted_case` and `malware:read` are granted the same way, and `casemgr-platform_admin` makes a platform administrator.
4. Require MFA for every login. In Keycloak, make the browser flow's OTP step required. In Entra ID or Okta, use a conditional access or sign-on policy for the app.
5. Create two break-glass accounts in the IdP itself, not federated from AD. Store their credentials offline, give them a hardware MFA token, and alert on every use.

The app re-reads each user's groups through the refresh token every `GROUP_SYNC_MINUTES` (default 5), so removing a user from a group takes effect within that time.

## 3. Secrets

The charts never create secrets. Provision them with External Secrets Operator (or another secret store) in the release namespace:

| Secret | Keys | Used by |
|---|---|---|
| `casemgr` | `CASEMGR_OIDC_CLIENT_SECRET`, `CASEMGR_SECRET_KEY` (32+ random bytes), and as needed `CASEMGR_S3_ACCESS_KEY`, `CASEMGR_S3_SECRET_KEY`, `CASEMGR_SMTP_USERNAME`, `CASEMGR_SMTP_PASSWORD`, `CASEMGR_IMAP_URL` | backend, jobs |
| `casemgr-db-runtime` | `username` (`casemgr_app`), `password`; type `kubernetes.io/basic-auth` | database chart (creates the role), backend, jobs |
| backup credentials (`backup.credentialsSecret`) | `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY` | database backups; omit when using IAM roles |

Prefer workload identity (IRSA, Azure Workload Identity, GKE Workload Identity) over static S3 keys. Leave the S3 keys out of `casemgr` and `backup.credentialsSecret` empty to use the pod's role.

## 4. Database and least-privilege roles

```sh
helm install casemgr-db deploy/helm/casemgr-db -n casemgr -f db-values.yaml
kubectl -n casemgr wait cluster/casemgr-db --for=condition=Ready --timeout=15m
```

- `casemgr` owns the schema. Only the migration hook uses it, through the `casemgr-db-app` secret that CloudNativePG creates.
- `casemgr_search` is a NOLOGIN role with BYPASSRLS, created when the cluster is initialized. It owns only the `search_hits` function, which applies the tenant and restricted-case filters itself, and has SELECT on the five searched tables. Row-level security would otherwise stop PostgreSQL from using the full-text indexes.
- `casemgr_app` is the runtime role. It owns no tables, so row-level security applies to it. It has only the grants the migrations give it: no UPDATE or DELETE on the audit log, chain-of-custody, or notification tables, and no DDL.
- Clients must use TLS. `pg_hba` rejects non-TLS connections, and the app connects with `ssl=verify-full` against the cluster CA (the `casemgr-db-ca` secret).
- Commits are synchronous to at least one standby (`dataDurability: required`), so a failover loses no committed data.
- The Cluster carries `helm.sh/resource-policy: keep`, so uninstalling the chart does not delete the database.

## 5. Object storage

Create two buckets, both with default encryption under a customer-managed KMS key and public access blocked:

- **Evidence** (`config.S3_BUCKET`). The pre-install hook `create_bucket` applies its CORS rule (PUT from `publicUrl` only) and a lifecycle rule that aborts multipart uploads left incomplete for 7 days. Give the app's role only `s3:ListBucket`, `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListMultipartUploadParts`, `s3:AbortMultipartUpload`, `s3:PutBucketCors`, and `s3:PutBucketLifecycleConfiguration` on this bucket. The retention job deletes evidence objects after purging a case, so evidence is not under Object Lock. Chain of custody is kept by the database's append-only log and the SHA-256 hash taken at upload.
- **Backups** (`backup.destinationPath`). Enable Object Lock in compliance mode with a default retention a little shorter than `backup.retention` (for example 28 days for 30), so backups cannot be altered or deleted early, including by an administrator. Barman's own retention cleanup then deletes objects only after their lock has expired. Use a separate account or project from the cluster if possible.

`config.S3_PUBLIC_ENDPOINT_URL` is the endpoint browsers upload to. The web app's Content-Security-Policy allows `connect-src` to it and nowhere else.

## 6. TLS and the Gateway

The app chart creates a Gateway with a single HTTPS listener (TLS 1.2 minimum, set by a ClientTrafficPolicy) and two routes. `/api/` goes to the backend, with a 1 h timeout for streamed evidence downloads, and everything else goes to the frontend. Set `gateway.clusterIssuer` to have cert-manager issue `gateway.tlsSecret` for `gateway.hostname`, for example through ACME or your internal CA.

The API route is rate limited with a local limit per Envoy replica (`gateway.apiRateLimit` requests per second). For limits per client, switch the BackendTrafficPolicy to Envoy Gateway's global rate limit, which needs Redis.

Encryption in transit:

| Path | How |
|---|---|
| Browser to Gateway | TLS 1.2+ (cert-manager) |
| App to PostgreSQL, and replication | TLS, verified (CloudNativePG CA) |
| App to S3, IdP, SMTP, IMAP | TLS (https, STARTTLS, imaps) |
| Gateway to pods | Cluster network. Use a CNI with WireGuard or IPsec encryption (Cilium, Calico), or a service mesh, if node-to-node traffic must be encrypted |

## 7. Network policies

The app chart denies all ingress and egress in the namespace by default, then allows only:

- DNS to kube-dns, for every pod.
- Ingress to the backend (8000) and frontend (8080) from the Gateway namespace (`networkPolicy.gatewayNamespace`).
- Egress from the backend and jobs to PostgreSQL (5432), and to `networkPolicy.egressPorts` (default 443, 587, 993) for the IdP, S3, webhooks, SMTP, and IMAP.

The database chart allows PostgreSQL connections only from pods labeled `casemgr.io/database-client: "true"` (the backend and jobs) and from its own instances, plus the operator's status checks. To narrow egress further, use a CNI with FQDN policies (for example Cilium's `toFQDNs`).

## 8. Install

```sh
helm install casemgr deploy/helm/casemgr -n casemgr -f values.yaml --wait
```

Minimum `values.yaml`:

```yaml
publicUrl: https://casemgr.example.com
images:
  backend: ghcr.io/<owner>/casemgr-backend@sha256:<digest>
  frontend: ghcr.io/<owner>/casemgr-frontend@sha256:<digest>
config:
  OIDC_METADATA_URL: https://idp.example.com/realms/casemgr/.well-known/openid-configuration
  SMTP_HOST: smtp.example.com
  MAIL_FROM: casemgr@example.com
  S3_REGION: us-east-1
  S3_BUCKET: example-casemgr-evidence
  S3_PUBLIC_ENDPOINT_URL: https://s3.us-east-1.amazonaws.com
gateway:
  hostname: casemgr.example.com
  clusterIssuer: letsencrypt
```

Every value is documented in `deploy/helm/casemgr/values.yaml` and `deploy/helm/casemgr-db/values.yaml`. The key ones:

| Value | Purpose |
|---|---|
| `config.*` | Settings passed as `CASEMGR_*` (see `backend/app/config.py`) |
| `backend.replicas`, `backend.maxReplicas` | HPA range. Raise the minimum ahead of a declared major incident |
| `jobs.<name>` | CronJob schedules. Set to `null` to disable a job |
| `gateway.apiRateLimit` | API requests per second per Envoy replica |
| `networkPolicy.egressPorts` | Outbound ports the backend may use |
| `instances`, `storage.*` (db) | Database size and an encrypted StorageClass |
| `backup.*` (db) | Bucket, KMS encryption, schedule, retention |

## 9. Verify images

Release images are signed with cosign (keyless, through GitHub's OIDC identity) and carry a signed SPDX SBOM attestation. The SBOMs are also attached to each GitHub release.

```sh
cosign verify ghcr.io/<owner>/casemgr-backend@sha256:<digest> \
  --certificate-identity-regexp 'https://github.com/<owner>/project_boxed_wine/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type spdxjson <same arguments>
```

Enforce this at admission with a Kyverno `verifyImages` policy for `ghcr.io/<owner>/casemgr-*` that has the same issuer and subject.

## 10. Upgrades

1. Read the release notes and check the new images' signatures.
2. Confirm a recent base backup exists and WAL archiving is healthy (`kubectl get cluster casemgr-db -o jsonpath='{.status.conditions}'`).
3. Run `helm upgrade casemgr ... --wait`. The `migrate` hook applies schema migrations as the owner role before new pods start, so old pods briefly run against the new schema during the rollout.
4. If the upgrade fails, run `helm rollback`. A migration cannot be rolled back that way; restore to a point in time instead (`docs/operations.md`).

Upgrade operators (CloudNativePG, Envoy Gateway, cert-manager) separately, following their own guides.

## 11. Pre-go-live checklist

- [ ] The IdP requires MFA for the client, and both break-glass accounts are tested and alerting.
- [ ] Groups are mapped for both tenants; there are no personal admin accounts beyond the minimum.
- [ ] The namespace enforces Pod Security `restricted`, and Kyverno verifies image signatures.
- [ ] Images are pinned by digest and signatures are verified.
- [ ] Secrets come from the secret manager; no static S3 keys where workload identity is available.
- [ ] The StorageClass and both buckets are encrypted under customer-managed keys; the backup bucket has Object Lock.
- [ ] The Gateway certificate is valid, and TLS 1.0 and 1.1 are refused (`openssl s_client -tls1_1` fails).
- [ ] The NetworkPolicies are in place: a pod in another namespace cannot reach the backend or PostgreSQL.
- [ ] The first base backup has completed, and a restore drill has passed (`docs/operations.md`).
- [ ] The `audit_verify` job runs daily, and its failures alert.
- [ ] The audit log, Gateway logs, and IdP logs are forwarded to the SIEM.
- [ ] A load test has met NFR-02 on production-sized hardware (`loadtest/`).
- [ ] The vulnerability disclosure contact is published, and the annual third-party penetration test is scheduled.

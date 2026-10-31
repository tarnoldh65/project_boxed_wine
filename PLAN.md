# Plan: MVP Cyber Incident Response Case Management Tool

Scope: every priority **M** item in `cyber-ir-case-management-requirements.md`, plus multi-tenancy (FR-101, a C item pulled in by decision). Other S and C items are out of scope unless an M item depends on them (noted inline). Each phase ends with its success criteria checked off; a phase is not started until the previous one is complete.

Status: **Phase 2 complete.**

---

## 1. Architecture and stack

| Concern | Choice | Why |
|---|---|---|
| Backend | Python (latest stable), FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, `uv` | Generates the OpenAPI spec for free (NFR-07); strong libraries for PDF/DOCX, zip, parsing |
| Frontend | React + TypeScript, Vite, TanStack Query, plain CSS (React Router when there are multiple pages) | SPA uses only the public REST API, so the API covers 100% of the UI (NFR-07) |
| API client | `openapi-fetch` with types generated from the committed `backend/openapi.json` | One source of truth; CI fails on drift |
| Database | PostgreSQL (latest stable) | Relational data, full-text search (GIN/tsvector), triggers for append-only tables |
| Object storage | S3 API (AWS S3, Ceph RGW, or similar) with SSE-KMS and Object Lock | Large evidence (50 GB via multipart presigned upload), immutable backups |
| Identity | OIDC only (Keycloak self-hosted, or Entra ID / Okta) | SSO + MFA enforced at the IdP; break-glass accounts live in the IdP (SR-01) |
| Background work | Kubernetes CronJobs running `python -m app.jobs <name>` | No queue or broker needed for MVP |
| Notifications | SMTP email + outbound webhooks | Covers escalation, @mentions, clock warnings |
| Reports | WeasyPrint (PDF), python-docx (DOCX) | FR-92 |
| Packaging | One backend image (API + jobs), one frontend image (static files on nginx-unprivileged), one Helm chart | Simple to deploy |

Key design decisions:
- **Auth:** the backend runs the OIDC authorization-code + PKCE flow and gives the browser an HttpOnly, Secure, SameSite=Strict session cookie (server-side session in Postgres, idle timeout). Users are created on first login; roles come from IdP group claims and are re-read through the refresh token every `CASEMGR_GROUP_SYNC_MINUTES` (default 5), so IdP changes take effect without a new login. MFA is enforced by the IdP's login flow (OTP always required). Machines use scoped, expiring API tokens (stored hashed).
- **Tenancy:** two tenants at launch, `detect-respond` and `insider-threat`, with full data segregation. Every tenant-owned table has a `tenant_id`. Postgres row-level security filters on `app.tenant_id`, which the backend sets per transaction from the tenant in the URL (`/api/tenants/{slug}/...`) after checking membership, so a missed filter in app code cannot leak data. Users can belong to several tenants with a different role in each, and they switch the active tenant in the UI. Admin configuration (states, fields, templates, SLAs, clocks) is per tenant. Evidence is stored under a per-tenant bucket prefix.
- **Authorization:** within a tenant, one query helper (`visible_cases(user)`) applies RBAC and case ACLs. Every case-scoped query, search included, goes through it. Restricted cases (e.g., executive compromise, HR/legal matters) are invisible, not just inaccessible (SR-02).
- **Append-only tables** (audit log, chain of custody, notification log): the app DB role has INSERT/SELECT only, a trigger rejects UPDATE/DELETE, and each row stores `hash = sha256(prev_hash || row)` so tampering is detectable. A `verify` job checks the chain.
- **Observables** are deduplicated (`unique(type, value)`) and linked to cases through a join table, so cross-case correlation is a single indexed lookup (FR-33).
- **Time:** all timestamps are `timestamptz` in UTC; the UI renders them in the user's timezone (NFR-09).

### Repository layout

```
backend/
  app/            main.py, config.py, db.py, auth.py, audit.py, one module per domain
  app/jobs/       sla, clocks, email_intake, evidence_hash, retention, audit_verify
  migrations/
  tests/
frontend/
  src/
  nginx.conf.template
  tests/
e2e/              Playwright acceptance tests
deploy/
  compose.yaml    local dev: Postgres, SeaweedFS (S3), Keycloak, Mailpit
  keycloak/       dev realm import
  helm/casemgr/
docs/
  deployment.md   secure deployment guide
  operations.md   backup/restore, DR, upgrades, key rotation
.github/workflows/ci.yml
.gitignore
README.md
```

### Testing approach (applies to every phase)

- Backend: pytest + pytest-asyncio against a real Postgres (testcontainers), with no DB mocks. Every service function and endpoint is tested, including permission-denied paths. Coverage gate: 90% lines on `backend/app`.
- Pure logic (observable extraction, severity matrix, SLA/clock math, gate rules, metrics) gets table-driven unit tests with edge cases.
- Frontend: Vitest + Testing Library for components with logic, colocated as `*.test.tsx`.
- E2E: Playwright against the compose stack. The five acceptance criteria in section 6 of the requirements become five E2E tests, with two adjustments: criterion 1 omits enrichment, and criterion 4 is met by tenant segregation (a user outside `insider-threat` gets zero results) as well as case ACLs.
- CI runs on every PR: lint (ruff, oxlint), type check (pyright, tsc), unit tests, E2E, Semgrep, dependency audit, Trivy image scan, Syft SBOM.

---

## 2. Phases

### Phase 0 - Scaffolding and CI

- [x] Latest stable versions of all tools and libraries verified and pinned (`uv.lock` with a 7-day dependency cooldown, `package-lock.json`, image digests, GitHub Actions pinned to commit SHAs)
- [x] `.gitignore` covers Python, Node, IDE, env files, local storage, coverage output
- [x] `backend/` FastAPI app with `/healthz` and `/readyz`; Alembic set up with an empty baseline migration
- [x] `frontend/` Vite React TS app builds and renders a shell layout
- [x] `deploy/compose.yaml` starts Postgres, S3 store, Keycloak (realm import with MFA required), Mailpit, backend, frontend
- [x] pytest with testcontainers Postgres runs a passing sample test; Vitest and Playwright run a passing sample test
- [x] CI workflow runs all lint, type, test, and security steps listed above (validated with actionlint; every step run locally and passing; first GitHub run pending push)
- [x] README states what the project is and how to run it locally (minimal)

### Phase 1 - Identity, tenancy, RBAC, audit log (SR-01, SR-02, SR-03, SR-06, NFR-09, FR-101)

- [x] OIDC login/logout through Keycloak; the session cookie has the correct flags; idle timeout enforced
- [x] Tenants table seeded with `detect-respond` and `insider-threat`; row-level security policy pattern in place for tenant-owned tables (`app/rls.py`); the app DB role cannot bypass RLS
- [x] Per-tenant roles: `admin`, `ir_lead`, `analyst`, `legal`, `executive` (read-only), `auditor`, plus permission flags `restricted_case` and `malware:read`; a global `platform_admin` manages tenants (memberships are managed as IdP groups)
- [x] Roles mapped from IdP groups (`casemgr-<tenant>-<role>`) on each login; removing a group revokes access within `CASEMGR_GROUP_SYNC_MINUTES` (default 5; 0 in the dev stack) via refresh-token re-sync, and a failed re-sync ends the session
- [x] Active-tenant switcher in the UI; API requests carry the tenant in the path and are rejected if the user is not a member
- [x] Scoped API tokens bound to one tenant: create, list, revoke, expire; stored as hashes; rotation = create new + revoke old
- [x] Audit log records every create/read/update/delete (including case views) with actor, tenant, action, target, timestamp, and hash chain; access denials and login failures are recorded too
- [x] Tests: UPDATE/DELETE on the audit table fails; the verify job detects an edited row; unauthenticated and under-privileged requests are rejected on every router; a query run with tenant A's context returns zero rows from tenant B on every tenant-owned table
- [x] Audit events are also emitted as JSON to stdout for SIEM collection

### Phase 2 - Admin configuration (FR-100, FR-11, FR-12, FR-20)

- [x] Per-tenant admin UI + API for: lifecycle states (default NIST set, served from code until a tenant customizes it), custom fields (text, picklist, date, user, number) required from a chosen state onward, severity matrix (impact x urgency; priority P1..Pn follows severity order), SLA targets per severity (edited with the matrix), notification/escalation rules (email and https webhook; reassignment is added with teams in Phase 4)
- [x] Config changes are audited (with the new section content) and take effect without restart; each change revalidates the whole configuration so cross-references (states, severities) stay consistent
- [x] Tests: severity matrix lookup for every cell; custom field validation by type; required-at-state enforcement (pure functions that Phase 3 applies to cases)

### Phase 3 - Cases, assets, intake (FR-01, FR-02, FR-04, FR-10, FR-14, FR-15)

- [ ] Case model with all FR-10 core fields, TLP/PAP, custom fields, regulated-data flags (PII/PHI/PCI/CUI + region), ATT&CK technique IDs (plain text list, needed for FR-91), resolution (incl. false positive)
- [ ] Severity/priority computed from the matrix; manual override requires a justification (FR-12)
- [ ] Assets (host, account, application, data set) with optional external CMDB ID, linked to cases
- [ ] Case templates per incident type (phishing, ransomware, BEC, insider, DDoS, data exposure, vulnerability exploitation) pre-load fields and playbook tasks
- [ ] Intake: UI create; `POST /api/alerts` (API token) and a generic webhook endpoint that map to a template; email intake job polls an IMAP mailbox and creates cases
- [ ] Alerts stored raw and linked to a case; alerts can be grouped into one case; cases can be merged (source closed as "merged into", all links moved) and split (selected alerts/observables move to a new case), with history kept in the audit log
- [ ] Teams have an on-call user; new intake cases are auto-assigned to them
- [ ] Tests: each intake path creates the correct case from a template; merge/split preserve every linked record; override without justification is rejected

### Phase 4 - Workflow, tasks, playbooks, SLAs, escalation (FR-21, FR-22, FR-23, FR-24, FR-60)

- [ ] Transitions validated against gate rules (required fields, all tasks resolved or waived with reason, PIR complete before Closed)
- [ ] Tasks: assignee, due date, status, dependencies (blocked until done), checklists, waive-with-reason
- [ ] Playbooks: ordered task lists with an optional condition (`field = value`), attached to case types; tasks generated on case creation and when the condition becomes true
- [ ] SLA timers (acknowledge, triage, contain, resolve) per severity; `sla` job sends warning and breach notifications and applies escalation rules (notify / reassign)
- [ ] "Declare major incident" action escalates per rules
- [ ] Tests: every gate rule pass/fail; dependency cycles rejected; SLA math across boundaries; escalation fires exactly once per breach

### Phase 5 - Observables and correlation (FR-30, FR-31, FR-33)

- [ ] Typed observables: IP (v4/v6), domain, URL, MD5, SHA1, SHA256, email, file name, registry key, user, process, CVE
- [ ] Extraction from pasted text, email bodies/headers, and log snippets, with defang (`hxxp`, `[.]`, `(dot)`, `[@]`) and refang; user confirms before saving
- [ ] Observable page lists every case it appears in within the active tenant (respecting ACLs); no cross-tenant correlation
- [ ] Tests: large table-driven extraction suite (true positives, false positives such as version numbers and file extensions, defanged forms, IPv6, overlapping matches)

### Phase 6 - Evidence and chain of custody (FR-40, FR-41, FR-42, FR-43, FR-44)

- [ ] Upload: browser does S3 multipart upload with presigned URLs (tested with a multi-GB file; designed for 50 GB+); external evidence references also supported
- [ ] `evidence_hash` job streams each new object and stores its SHA-256; downloads stream through the API, re-hash, and record the result
- [ ] Chain-of-custody entries (collected by/when/where/how, transfers, every access) in an append-only, hash-chained table; custody report export
- [ ] Malware samples: direct upload through the API (size-capped), wrapped server-side in an AES zip (password `infected`) before storage; download requires `malware:read`
- [ ] All downloads served as `Content-Disposition: attachment`, never rendered inline
- [ ] Legal hold flag on case blocks evidence deletion
- [ ] Tests: hash mismatch is detected and logged; custody table rejects UPDATE/DELETE; user without `malware:read` gets 403; delete under legal hold fails

### Phase 7 - Timeline, notes, comments, privilege (FR-50, FR-52, FR-54, FR-70, FR-74)

- [ ] Timeline events with real event time (distinct from entry time), source, and links to observable/asset/evidence; sorted chronologically
- [ ] Milestones: first compromise, detection, containment, eradication
- [ ] Markdown notes (sanitized rendering, no raw HTML) with code blocks, revision history, and @mentions that email the mentioned user
- [ ] Per-case comment stream
- [ ] Privilege marking on cases and notes; shown as a banner and stamped on reports
- [ ] Tests: XSS payloads in notes are neutralized; revision history is complete; mentions notify only users who can see the case

### Phase 8 - Regulatory clocks and notifications (FR-80, FR-81, FR-82)

- [ ] Configurable regimes: name, trigger (regulated-data flag + region, or materiality decision), duration, calendar vs business days, warning threshold; seeded with GDPR 72h, SEC 8-K 4 business days, HIPAA, and an editable state-law/NIS2/DORA/CIRCIA set
- [ ] Clocks shown on the case and the dashboard; `clocks` job sends warning notifications
- [ ] Materiality decision record: decision maker, date, rationale; starts dependent clocks
- [ ] Notification log (regulator, law enforcement, insurer, customer) with attachments; append-only
- [ ] Tests: deadline math for each regime including weekends and US federal holidays; GDPR clock warns at 48h

### Phase 9 - Search, dashboards, metrics, reports, PIR, retention (FR-90 to FR-94, FR-102)

- [ ] Full-text search across cases, notes, observables, and attachment metadata via Postgres FTS; saved searches; results filtered through `visible_cases`
- [ ] Dashboard: open cases by severity/status, SLA compliance, analyst workload, aging, active clocks
- [ ] Metrics: MTTD, MTTA, MTTC, MTTR, dwell time, false-positive rate, volume by category and ATT&CK technique, recurrence (new case sharing an observable with a closed case)
- [ ] PIR module: root cause, contributing factors, what went well / didn't, action items tracked to closure
- [ ] Reports (executive summary, technical, PIR) to PDF and DOCX, with privilege marking when set; layouts are fixed in code for MVP
- [ ] Retention policies per case type; `retention` job purges expired cases and evidence, skipping legal holds, and audits each purge
- [ ] Tests: metric calculations on fixed fixtures; restricted content never appears in search for unauthorized users; purge respects legal hold

### Phase 10 - Deployment, hardening, documentation (SR-04, SR-08, NFR-01, NFR-02, NFR-06, NFR-07)

- [ ] Helm chart: backend (>= 3 replicas, PodDisruptionBudget, HPA), frontend, CronJobs, NetworkPolicies (default deny), non-root read-only containers, Pod Security `restricted`
- [ ] Postgres via CloudNativePG: 3 instances, synchronous replica, WAL archiving to object storage (RPO <= 15 min), scheduled base backups to an Object Lock bucket
- [ ] TLS 1.2+ at the Gateway (cert-manager) and between app and Postgres; encrypted volumes and SSE-KMS for buckets
- [ ] Rate limiting on the API at the Gateway
- [ ] Images signed with cosign; SBOM published per release
- [ ] Load test (k6) against a dataset seeded to 1M cases / 100M observables: case view p95 < 2s, search p95 < 3s
- [ ] Restore drill performed from backup into a fresh namespace within RTO 4h; procedure written in `docs/operations.md`
- [ ] `docs/deployment.md` covers the secure deployment steps (below); README links to it
- [ ] All five acceptance-criteria E2E tests pass on the Helm deployment (kind cluster in CI)

---

## 3. Recommended deployment platforms and tools

| Need | Recommendation |
|---|---|
| Kubernetes | Managed: EKS, AKS, or GKE. Self-hosted / air-gapped: RKE2 or OpenShift |
| Ingress | Gateway API with Envoy Gateway (TLS, rate limiting) |
| Certificates | cert-manager |
| Postgres | CloudNativePG operator (HA, PITR, backups to object storage) |
| Object storage | Cloud S3 with KMS + Object Lock; on-prem Ceph RGW |
| Identity | Keycloak (self-hosted, separate from corporate AD for out-of-band resilience) or Entra ID / Okta |
| Secrets | External Secrets Operator backed by HashiCorp Vault or cloud KMS/secret manager |
| GitOps | Argo CD |
| Policy | Kyverno (require signed images, block privileged pods) |
| Observability | OpenTelemetry, Prometheus, Grafana, Loki; audit stream forwarded to the SIEM |
| Supply chain | Semgrep, Trivy, Syft, cosign, Dependabot/Renovate |

`docs/deployment.md` will cover: cluster prerequisites, IdP setup with MFA enforcement and break-glass accounts, secret provisioning, TLS, network policies, storage encryption and key management, backup configuration, least-privilege DB roles, Helm values reference, upgrade procedure, and a pre-go-live security checklist.

---

## 4. Requirement traceability (M items)

| Phase | Requirements |
|---|---|
| 1 | SR-01, SR-02, SR-03, SR-06, NFR-09, FR-101 (C, by decision) |
| 2 | FR-11, FR-12, FR-20, FR-100 |
| 3 | FR-01, FR-02, FR-04, FR-10, FR-14, FR-15 |
| 4 | FR-21, FR-22, FR-23, FR-24, FR-60 |
| 5 | FR-30, FR-31, FR-33 |
| 6 | FR-40, FR-41, FR-42, FR-43, FR-44 |
| 7 | FR-50, FR-52, FR-54, FR-70, FR-74 |
| 8 | FR-80, FR-81, FR-82 |
| 9 | FR-90, FR-91, FR-92, FR-93, FR-94, FR-102 |
| 10 | SR-04, SR-08, NFR-01, NFR-02, NFR-06, NFR-07 |

SR-08's annual third-party pentest and vulnerability disclosure program are organizational processes; the plan covers the SDLC tooling and SBOM.

---

## 5. Decisions

1. Enrichment (FR-32) is deferred; acceptance criterion 1 is tested without it.
2. STIX/TAXII is deferred, including the STIX/TAXII part of NFR-07. The REST API, webhooks, and OpenAPI spec remain in scope.
3. Multi-tenant from the start: `detect-respond` and `insider-threat`, with full data segregation.
4. Stack: FastAPI + React.
5. Report layouts are fixed in code for MVP; editable report templates may come later.

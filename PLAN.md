# Plan: MVP Cyber Incident Response Case Management Tool

Scope: every priority **M** item in `cyber-ir-case-management-requirements.md`, plus multi-tenancy (FR-101, a C item pulled in by decision). Other S and C items are out of scope unless an M item depends on them (noted inline). Each phase ends with its success criteria checked off; a phase is not started until the previous one is complete.

Status: **Phase 15 complete.** All phases done, including the UI redesign (section 2a).

---

## 1. Architecture and stack

| Concern | Choice | Why |
|---|---|---|
| Backend | Python (latest stable), FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, `uv` | Generates the OpenAPI spec for free (NFR-07); strong libraries for PDF/DOCX, zip, parsing |
| Frontend | React + TypeScript, Vite, React Router, TanStack Query, plain CSS | SPA uses only the public REST API, so the API covers 100% of the UI (NFR-07) |
| API client | `openapi-fetch` with types generated from the committed `backend/openapi.json` | One source of truth; CI fails on drift |
| Database | PostgreSQL (latest stable) | Relational data, full-text search (GIN/tsvector), triggers for append-only tables |
| Object storage | S3 API (AWS S3, Ceph RGW, or similar) with SSE-KMS and Object Lock | Large evidence (50 GB via multipart presigned upload), immutable backups |
| Identity | OIDC only (Keycloak self-hosted, or Entra ID / Okta) | SSO + MFA enforced at the IdP; break-glass accounts live in the IdP (SR-01) |
| Background work | Kubernetes CronJobs running `python -m app.jobs <name>` | No queue or broker needed for MVP |
| Notifications | SMTP email + outbound webhooks | Covers escalation, @mentions, clock warnings |
| Reports | fpdf2 with a bundled DejaVu font (PDF), python-docx (DOCX) | FR-92; pure Python, no system libraries |
| Packaging | One backend image (API + jobs), one frontend image (static files on nginx-unprivileged), one Helm chart | Simple to deploy |

Key design decisions:
- **Auth:** the backend runs the OIDC authorization-code + PKCE flow and gives the browser an HttpOnly, Secure, SameSite=Strict session cookie (server-side session in Postgres, idle timeout). Users are created on first login; roles come from IdP group claims and are re-read through the refresh token every `CASEMGR_GROUP_SYNC_MINUTES` (default 5), so IdP changes take effect without a new login. MFA is enforced by the IdP's login flow (OTP always required). Machines use scoped, expiring API tokens (stored hashed).
- **Tenancy:** two tenants at launch, `detect-respond` and `insider-threat`, with full data segregation. Every tenant-owned table has a `tenant_id`. Postgres row-level security filters on `app.tenant_id`, which the backend sets per transaction from the tenant in the URL (`/api/tenants/{slug}/...`) after checking membership, so a missed filter in app code cannot leak data. Users can belong to several tenants with a different role in each, and they switch the active tenant in the UI. Admin configuration (states, fields, templates, SLAs, clocks) is per tenant. Evidence is stored under a per-tenant bucket prefix.
- **Authorization:** within a tenant, one query helper (`visible_cases(user)`) applies RBAC and case ACLs. Every case-scoped query goes through it; search runs in the `search_hits` database function, which applies the same rule (Phase 10). Restricted cases (e.g., executive compromise, HR/legal matters) are invisible, not just inaccessible (SR-02).
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
  compose.yaml    local dev: Postgres, SeaweedFS (S3), Keycloak, Mailpit, GreenMail (intake mailbox)
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

- [x] Case model with all FR-10 core fields (per-tenant case numbers), TLP/PAP, custom fields, regulated-data flags (PII/PHI/PCI/CUI + region), ATT&CK technique IDs (plain text list, needed for FR-91), resolution (incl. false positive); closing requires a resolution
- [x] Severity/priority computed from the matrix; manual override requires a justification (FR-12)
- [x] Assets (host, account, application, data set) with optional external CMDB ID, linked to cases
- [x] Case templates per incident type (phishing, ransomware, BEC, insider, DDoS, data exposure, vulnerability exploitation) are tenant configuration and pre-load category, impact, urgency, TLP/PAP, and team; their playbook tasks arrive with tasks in Phase 4
- [x] Intake: UI create; `POST /api/tenants/{slug}/alerts` and a generic webhook `POST /api/tenants/{slug}/webhooks/{source}?template=...` (query parameters name the title, id, and group fields) using API tokens with the `alerts:write` scope; the `email_intake` job polls an IMAP mailbox and creates cases. Alerts are de-duplicated by source and external id (Message-ID for email)
- [x] Alerts stored raw and linked to a case; alerts sharing a group key join the same open case; cases can be merged (source closed as "merged into", all links moved) and split (selected alerts/assets move to a new case), with history kept in the audit log. Observables, tasks, and notes join merge/split in their phases
- [x] Teams have an on-call user; new cases (intake and manual) are assigned to the on-call member of the template's team
- [x] Restricted cases are invisible (404) without `restricted_case`; custom fields are enforced on every case update for the case's state (creation is exempt so intake never drops an alert)
- [x] Tests: each intake path creates the correct case from a template; merge/split preserve every linked record; override without justification is rejected

### Phase 4 - Workflow, tasks, playbooks, SLAs, escalation (FR-21, FR-22, FR-23, FR-24, FR-60)

- [x] Transitions validated against gate rules: required custom fields, a resolution to close, and (per state, on by default for Closed) every task done or waived. The PIR gate is added with the PIR module in Phase 9
- [x] Tasks: assignee, due date, status, dependencies (blocked until done or waived), checklists, waive-with-reason; tasks of closed cases are read-only; merge moves tasks
- [x] Playbooks: ordered task lists per case template with an optional condition (`field = value` on severity, impact, urgency, TLP, PAP, team, or a custom field); tasks generated on case creation and when the condition becomes true; the seven default templates ship with playbooks
- [x] SLA timers (acknowledge, triage, contain, resolve) per severity in calendar time from case creation, warning at 75% of the target; acknowledge is an explicit action (or any move past the first state), triage and contain are met by states marked as those milestones, resolve by closing. The `sla` job fires warning and breach escalations and delivers them; escalation rules notify (email, https webhook) and/or reassign to a team's on-call member. Rules fire through a notifications outbox, so requests never wait on mail or webhooks, and messages carry only the case number, severity, event, and link
- [x] "Declare major incident" action records the reason and escalates per rules
- [x] Tests: every gate rule pass/fail; dependency cycles rejected; SLA math across boundaries; escalation fires exactly once per breach

### Phase 5 - Observables and correlation (FR-30, FR-31, FR-33)

- [x] Typed observables: IPv4, IPv6, domain, URL, MD5, SHA1, SHA256, email, file name, registry key, user, process, CVE; values are normalized (re-fanged, lowercased where case-insensitive, IPv6 compressed) and de-duplicated per tenant
- [x] Extraction from pasted text, email bodies/headers, and log snippets (and from a case's alert payloads), with defang (`hxxp`, `[.]`, `(dot)`, `[@]`, `[:]`) and refang; values are displayed defanged; the user reviews candidates before linking. Users and processes are added by hand (free text has no reliable pattern for them)
- [x] Observable page lists every case it appears in within the active tenant (respecting ACLs: restricted cases are neither listed nor counted); case pages show how many other cases share each observable; no cross-tenant correlation. Merge and split carry observables
- [x] Tests: large table-driven extraction suite (true positives, false positives such as version numbers, file extensions, times, MAC addresses and C++ scope operators, defanged forms, IPv6, overlapping matches)

### Phase 6 - Evidence and chain of custody (FR-40, FR-41, FR-42, FR-43, FR-44)

- [x] Upload: browser does S3 multipart upload with presigned URLs (parts of 64 MiB or more, up to S3's 10,000 parts); the server completes the upload from S3's own part list. Verified with a 2.27 GB object (34 parts) against the dev S3; external evidence references also supported
- [x] `evidence_hash` job streams each new object and stores its SHA-256; downloads stream through the API, re-hash, and record `verified` or `mismatch` (a mismatch also flags the item and is audited)
- [x] Chain-of-custody entries (collected by/when/where/how, upload, hashing, transfers, every access, verification, deletion) in an append-only table, hash-chained per evidence item; custody report with chain verification, savable as JSON
- [x] Malware samples: direct upload through the API (size-capped, 100 MB by default), wrapped server-side in an AES zip (password `infected`) before storage; download requires `malware:read`
- [x] All downloads served as `Content-Disposition: attachment` and `application/octet-stream`, never rendered inline
- [x] Legal hold on a case (set through its own endpoint with a reason, by holders of `legal:manage`: legal, IR leads, admins) blocks evidence deletion; deletion otherwise requires a reason and keeps the custody record
- [x] Tests: hash mismatch is detected and logged; custody table rejects UPDATE/DELETE; user without `malware:read` gets 403; delete under legal hold fails

### Phase 7 - Timeline, notes, comments, privilege (FR-50, FR-52, FR-54, FR-70, FR-74)

- [x] Timeline events with real event time (distinct from entry time, stored in UTC), source, and links to an observable, asset, or evidence item of the same case; sorted chronologically
- [x] Milestones: first compromise, detection, containment, eradication, as flags on timeline events (one each per case); dwell time shown from first compromise to detection
- [x] Markdown notes (raw HTML never rendered, unsafe link targets dropped) with code blocks, append-only revision history, and @mentions that email the mentioned user once per note through the notifications outbox (the email carries only a link)
- [x] Per-case comment stream (append-only, plain text)
- [x] Privilege marking on cases (set by holders of `legal:manage`, with a reason) and on notes (by their authors); shown as a banner. Report stamping is part of Phase 9 reports. Merge moves the timeline, notes, and comments
- [x] Tests: XSS payloads in notes are neutralized; revision history is complete; mentions notify only users who can see the case

### Phase 8 - Regulatory clocks and notifications (FR-80, FR-81, FR-82)

- [x] Configurable regimes: name, trigger (regulated-data flag + region, or materiality decision), duration, calendar vs business days, warning threshold; seeded with GDPR 72h, SEC 8-K 4 business days, HIPAA, and an editable state-law/NIS2/DORA/CIRCIA set. A clock's deadline is fixed when it starts, so later configuration edits do not move it
- [x] Clocks shown on the case and on the cases page (the Phase 9 dashboard reuses `GET /clocks`); `clocks` job fires `clock_warning` and `clock_breach` escalation rules
- [x] Materiality decision record: decision maker, date, rationale; a material decision starts dependent clocks from the decision date
- [x] Notification log (regulator, law enforcement, insurer, customer) with case evidence as attachments; append-only, and logging a notification against a clock meets it. Merge moves clocks, decisions, and the log; where both cases run the same regime, the earlier deadline is kept
- [x] Tests: deadline math for each regime including weekends and US federal holidays; GDPR clock warns at 48h

### Phase 9 - Search, dashboards, metrics, reports, PIR, retention (FR-90 to FR-94, FR-102)

- [x] Full-text search across cases, notes, observables, and attachment metadata via Postgres FTS (generated `tsvector` columns with GIN indexes); per-user saved searches; results filtered through `visible_cases`
- [x] Dashboard: open cases by severity/status, SLA compliance, analyst workload, aging, active clocks, open PIR actions
- [x] Metrics: MTTD, MTTA, MTTC, MTTR, dwell time, false-positive rate, volume by category and ATT&CK technique, recurrence (new case sharing an observable with a closed case). Definitions are in `backend/app/analytics.py`
- [x] PIR module: root cause, contributing factors, what went well / didn't, action items tracked to closure; a true-positive case cannot enter a state with the PIR gate (on for Closed, including saved configurations) until its PIR is complete
- [x] Reports (executive summary, technical, PIR) to PDF (fpdf2, bundled DejaVu font) and DOCX, stamped with TLP and with the privilege marking when the case or a note is privileged; note Markdown is reproduced as plain source text; layouts are fixed in code for MVP
- [x] Retention days per case type; `retention` job purges expired cases and evidence objects, skipping legal holds, and audits each purge. The purge runs in a `SECURITY DEFINER` database function that itself refuses open cases and cases under legal hold
- [x] Tests: metric calculations on fixed fixtures; restricted content never appears in search for unauthorized users; purge respects legal hold

### Phase 10 - Deployment, hardening, documentation (SR-04, SR-08, NFR-01, NFR-02, NFR-06, NFR-07)

- [x] Helm chart `deploy/helm/casemgr`: backend (>= 3 replicas, PodDisruptionBudget, HPA), frontend, CronJobs, NetworkPolicies (default deny), non-root read-only containers, Pod Security `restricted`. Migrations and bucket setup run as pre-install/pre-upgrade hooks
- [x] Postgres via CloudNativePG in a separate chart `deploy/helm/casemgr-db` (kept on uninstall): 3 instances, synchronous replica, WAL archiving through the Barman Cloud plugin (`archive_timeout` 5 min, so RPO <= 15 min), daily base backups to an Object Lock bucket
- [x] TLS 1.2+ at the Gateway (cert-manager; TLS 1.1 refused) and verified TLS between app and Postgres (`ssl=verify-full`, non-TLS rejected by `pg_hba`); encrypted volumes and SSE-KMS for buckets documented and configurable
- [x] Evidence bucket: the `create_bucket` hook applies CORS (PUT from the app origin) and a lifecycle rule aborting incomplete multipart uploads after 7 days; `CASEMGR_S3_PUBLIC_ORIGIN` in the frontend CSP; `evidence_hash` and the other jobs as CronJobs
- [x] Rate limiting on the API route at the Gateway (Envoy Gateway local rate limit; per-client limits need the global limiter and Redis, documented)
- [x] Images signed with cosign (keyless) with a signed SPDX SBOM attestation, and SBOMs attached to each GitHub release (`release.yml`, on `v*` tags)
- [x] Load test (k6, `loadtest/`) run locally at 1M cases / 10M observables, by decision; the 100M run is for staging. Result: case view p95 886 ms, search p95 2.88 s with PostgreSQL on 2 CPUs. The first run failed: row-level security kept Postgres from using the full-text indexes (`@@` is not leakproof), so search now runs in a `search_hits` function owned by `casemgr_search` (NOLOGIN, BYPASSRLS) that applies the tenant and restricted-case filters itself, and excerpts are built only for returned hits
- [x] Restore drill (`deploy/kind/restore-drill.sh`, also in CI) restores into a fresh namespace from the backups, deploys the app, verifies the audit chain and row counts: 93 s locally; procedure in `docs/operations.md`
- [x] `docs/deployment.md` covers the secure deployment steps (below); README links to it
- [x] All E2E tests (24, including the five acceptance criteria) pass on the Helm deployment on kind, locally and as the `e2e-kind` CI job. The specs run jobs through `kubectl exec` when `E2E_TARGET=kind`

---

## 2a. UI redesign: frame workspace

Goal: replace the tabbed pages with a sleek, interactive workspace. Everything lives on two pages: **Workspace** and **Settings** (admins only). The Workspace is a grid of frames that users move and resize; selecting something in one frame opens related frames. Light and dark themes are both first-class.

Approach:
- **Frames:** `react-grid-layout` 2.x handles drag and resize; the workspace picks the breakpoint itself. It supports React 19 and injects no styles, so the CSP stays as it is. It has no keyboard support, so each frame also gets a menu for moving and resizing.
- **Reuse:** existing panels (tasks, notes, evidence, and so on) move into frames with their logic and tests intact. The one API change is server-side filtering of the case list, added in Phase 14 by decision.
- **Look:** CSS custom properties for colour, spacing, and elevation; `lucide-react` icons; Inter, self-hosted because the CSP blocks font CDNs. Charts are small hand-written components, so no chart library is added.
- **Addresses:** `/t/:slug`, `/t/:slug/cases/:n`, and `/t/:slug/observables/:id` keep working and open the matching frames, so links stay shareable. `/dashboard` and `/search?q=` open their frames in the Workspace.
- **Saved layout:** kept in the browser's localStorage per user and tenant, holding frame ids and positions only, never case data. The page works normally if storage is unavailable.
- **Narrow windows:** frames reflow by breakpoint and stack in a single column on phones, where dragging is turned off.

Frames and what opens them:

| Frame | Opens from | Selecting inside it opens |
|---|---|---|
| Case queue, Overview (KPIs and charts), Notification deadlines, Metrics, Search | Always available from the frame launcher | A case's frames |
| Case (header, actions, and a tab per section) | Selecting a case | A section popped out of its tab, docked under the case |
| Notes, Comments, Observables, Evidence, Assets, Regulatory, Post-incident review, Reports | Case summary chips | See the next rows |
| Observable correlation | Selecting an observable | Another case |
| Chain of custody | Selecting an evidence item | - |

### Phase 11 - Design system and themes

- [x] Colour, type, spacing, radius, and elevation tokens for light and dark, each colour defined once with CSS `light-dark()`; `index.css` restyled on them, including forms, tables, buttons, badges, and alerts
- [x] Theme control with Light, Dark, and System. The default follows the operating system; the choice is remembered, and `public/theme.js` applies it before first paint, so the page does not flash the wrong theme on load
- [x] Severity, TLP, SLA, and state badges meet WCAG AA contrast in both themes (`contrast.test.ts` reads the token pairs from the CSS), and each shows its status as text, so none relies on colour alone
- [x] Tests: theme selection and persistence, including when storage is unavailable; existing frontend tests pass

### Phase 12 - Frame workspace

- [x] `Frame` component (title bar, drag handle, collapse, maximize, close, menu) and `Workspace` grid with launcher and "Reset layout". The existing queue, dashboard, search, case, and observable pages run inside frames until Phases 13 and 14 split them
- [x] Frames drag and resize with the mouse, and the same moves work from the keyboard through the frame menu
- [x] Layout reflows at each breakpoint with no horizontal page scroll down to 360 px, and stacks in one column on phones
- [x] Layout is saved per user and tenant, restored on return, and a corrupt or outdated saved layout falls back to the default
- [x] Top bar replaces the tabs: tenant switcher, global search box, launcher, theme control, user menu
- [x] The address drives selection, so every existing link opens or updates a frame, and frames stay open as it moves on. Any successful change refreshes all open frames
- [x] Tests: layout save, restore, and fallback; opening, closing, collapsing, and keyboard moves; `workspace.spec.ts` drags, resizes, reloads, and checks window sizes in a real browser, with no CSP violations

### Phase 13 - Queue and insight frames

- [x] Case queue with sort and filter (state, severity, owner, text), a severity marker per row, the open case highlighted, and New case in a panel without leaving the page. Filtering moved to the server in Phase 14
- [x] Overview frame: KPI tiles and bar charts in place of the dashboard tables. Each chart is a styled table, so the figures are readable without the bars; bar fills were checked with the palette validator in both themes
- [x] Selecting a KPI or chart row filters the queue to the open cases it counts, and selecting it again clears the filter; SLA compliance opens the Metrics frame
- [x] Notification deadlines, Metrics, and Search frames, with saved searches; a search hit opens its case
- [x] `/dashboard` and `/search?q=` open these frames; restricted cases stay out of every frame for users without the grant
- [x] Tests: queue filtering and sorting, chart selection, deep links

### Phase 14 - Case frames and drill-down

- [x] Selecting a case opens Summary, Tasks, and Timeline; a second selection swaps the active case in the same frames, and the frame arrangement is kept
- [x] `CasePage` is split into a summary frame and a frame for each part of the case, titled with the case number; summary chips open the rest and show counts
- [x] Case actions (state change, merge, split, legal hold, privilege, major incident) open in dialogs from the summary; the privilege banner stays visible in every frame of a privileged case
- [x] Selecting an observable opens Observable correlation; selecting an evidence item opens Chain of custody, which closes when another case is selected; both follow links to other cases
- [x] Frames for one case share one copy of it and refresh together after a change. The Details form keeps only the user's edits, so unsaved text survives and other frames' changes show through
- [x] The case queue moved to the server, by decision, so every match stays reachable: `GET /cases` filters, sorts, and pages, returns `{cases, total}`, and the queue loads more on request. `open_only` counts as the dashboard does. SLA status is computed, so filtering or sorting on it runs over the matching cases in Python
- [x] Tests: panel tests moved to frames with their assertions kept; opening, swapping, and drill-down; privilege banner; `test_queue.py` covers every filter, sort, page, and restricted cases

### Phase 15 - Settings page, polish, and verification

- [x] Settings restyled with side navigation in the new design; all sections stay mounted, so unsaved edits are safe while moving between them
- [x] Transitions for opening and moving frames, turned off under `prefers-reduced-motion`; loading skeletons and empty states
- [x] Keyboard focus is visible and logical across frames: tab order follows the on-screen layout, and keyboard moves keep focus. An automated accessibility scan (axe) finds no serious issues in either theme; `contrast.test.ts` covers elements it cannot measure when scrolled out of view
- [x] Visual check of every frame in both themes at 360, 768, 1280, and 1920 px, with no sideways scrolling or console errors
- [x] Playwright specs updated for the new navigation, plus specs for moving a frame, reloading, and seeing it restored, and for the page settling after a breakpoint change; all 35 pass on compose and on kind with the CSP unchanged, and the restore drill passes
- [x] README updated; frontend lint, type check, tests, build, npm audit, Semgrep, and Trivy clean
- [x] Phones: frames take their natural height, so the page does the scrolling. Bundle: Settings and the Markdown renderer load on demand, and the first load drops from 651 KB to 465 KB
- [x] The grid is the plain controlled `GridLayout`, and only moves a person makes are saved. `ResponsiveGridLayout` kept a second copy of the layout that fought ours in an endless render loop after any breakpoint change, and stored layouts under the wrong breakpoint; saved layouts from before the fix are discarded

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
6. UI redesign: one active case at a time (selecting another swaps the case frames' content), layouts saved in the browser per user and tenant, frames on a snapping grid that never overlap, and a dense operations-console look with neutral surfaces, one accent colour, and colour reserved for severity and status.
7. Case sections are tabs of the case frame, kept mounted once visited so typing survives switching. Any section can pop out into its own frame, docked directly under the case, and go back as a tab. This replaced opening a frame per section wherever there was space, which scattered a case across the workspace.


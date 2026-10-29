# Cyber Incident Response Case Management Tool: Requirements

**Priority key:** **M** = Must (MVP), **S** = Should, **C** = Could (later)

---

## 1. Purpose and scope

A system of record for security incidents, from detection through post-incident review. It gives SOC analysts, IR leads, legal, and leadership one place for case data, evidence, tasks, timelines, and reporting. It sits alongside the SIEM, SOAR, EDR, and ticketing tools; it does not replace them.

**Users:** Tier 1–3 SOC analysts, IR lead / incident commander, threat intel analysts, forensic examiners, legal/privacy/compliance, executives (read-only), external IR firms (limited guest access), and auditors.

---

## 2. Functional requirements

### 2.1 Case intake and creation

| ID | Requirement | Pri |
|---|---|---|
| FR-01 | Create cases manually through the UI and automatically through the API, email, or webhooks from SIEM, EDR, and SOAR. | M |
| FR-02 | Group related alerts into a single case, and merge or split cases while keeping the history. | M |
| FR-03 | Detect duplicates by matching observables (IP, hash, host, user) against open cases. | S |
| FR-04 | Provide case templates by incident type (phishing, ransomware, BEC, insider, DDoS, data exposure, vulnerability exploitation) that pre-load tasks, fields, and playbooks. | M |
| FR-05 | Support a separate "alert" or "event" stage with promote-to-case and dismiss-as-false-positive actions, each requiring a reason. | S |

### 2.2 Case data model

| ID | Requirement | Pri |
|---|---|---|
| FR-10 | Core fields: ID, title, description, status, severity, priority, category, owner, team, created, detected, and closed times, source, affected business units, and TLP/PAP marking. | M |
| FR-11 | Admins can add custom fields (text, picklist, date, user, number), and can make a field required at a given workflow stage. | M |
| FR-12 | Severity and priority come from a configurable matrix (impact × urgency), with manual override and a required justification. | M |
| FR-13 | Classification maps to frameworks: MITRE ATT&CK tactics and techniques, VERIS, and NIST 800-61 categories. | S |
| FR-14 | Track affected assets (hosts, accounts, applications, data sets), linked to the CMDB or asset inventory where one exists. | M |
| FR-15 | Flag whether regulated data is involved (PII, PHI, PCI, CUI) and drive notification workflows from that flag. | M |

### 2.3 Workflow and lifecycle

| ID | Requirement | Pri |
|---|---|---|
| FR-20 | Configurable lifecycle states, by default aligned to NIST: New → Triage → Analysis → Containment → Eradication → Recovery → Post-Incident → Closed. | M |
| FR-21 | Gate state transitions on rules, e.g. a case cannot close until the root-cause and lessons-learned fields are filled in. | M |
| FR-22 | Tasks with assignee, due date, status, dependencies, and checklists, with tasks generated automatically from templates or playbooks. | M |
| FR-23 | Track SLAs per severity (time to acknowledge, triage, contain, and resolve), with breach warnings and escalation. | M |
| FR-24 | Escalation paths: notify or reassign automatically based on severity, SLA breach, or a manual "declare major incident" action. | M |
| FR-25 | Shift handoff: a handoff summary view, with case ownership transfer and an acknowledgement step. | S |
| FR-26 | Approval steps for sensitive actions such as taking production systems offline, contacting law enforcement, or notifying externally. | S |

### 2.4 Observables, IOCs, and enrichment

| ID | Requirement | Pri |
|---|---|---|
| FR-30 | Store typed observables: IP, domain, URL, hash (MD5, SHA1, SHA256), email, file name, registry key, user, process, CVE. | M |
| FR-31 | Auto-extract observables from pasted text, emails, and log snippets, including de-fanging and re-fanging. | M |
| FR-32 | Enrich observables on demand or automatically through connectors (VirusTotal, threat intel platform, WHOIS, passive DNS, internal sightings). | S |
| FR-33 | Correlate across cases: show every case where an observable has appeared. | M |
| FR-34 | Flag IOCs for export or sharing (STIX 2.1 / TAXII, MISP) and push them to blocking controls through SOAR. | S |
| FR-35 | Mark observables as malicious, suspicious, benign, or unknown, with confidence and an expiry or age-out date. | S |

### 2.5 Evidence and chain of custody

| ID | Requirement | Pri |
|---|---|---|
| FR-40 | Upload attachments (logs, PCAPs, memory images, screenshots, emails), and support large files of at least 50 GB through chunked upload or references to external evidence storage. | M |
| FR-41 | Compute hashes (SHA-256) automatically when evidence is ingested, and verify them when it is accessed. | M |
| FR-42 | Chain-of-custody log for every evidence item: who collected it, when, where, and how, every transfer, and every access. The log cannot be modified. | M |
| FR-43 | Legal hold flag that blocks deletion or retention expiry for the case and its evidence. | M |
| FR-44 | Malware samples are stored in a quarantined, non-executable form (password-protected zip), and access to them requires elevated permission. | M |
| FR-45 | In-browser preview of safe file types; dangerous types are never rendered. | S |

### 2.6 Timeline and investigation

| ID | Requirement | Pri |
|---|---|---|
| FR-50 | Incident timeline: analysts add events with their actual timestamps (not entry time), source, and the linked observable, asset, or evidence. | M |
| FR-51 | Import timeline events from CSV, EDR or SIEM queries, or forensic tool output (e.g. plaso / log2timeline). | S |
| FR-52 | Investigation notes in rich text or Markdown, with @mentions, revision history, and code/log blocks. | M |
| FR-53 | Link graph showing relationships between cases, assets, users, and observables. | C |
| FR-54 | Record key milestones: first compromise, detection, containment, and eradication, used for dwell-time metrics. | M |

### 2.7 Playbooks and automation

| ID | Requirement | Pri |
|---|---|---|
| FR-60 | Build playbooks as ordered or conditional task lists linked to case types. | M |
| FR-61 | Trigger rules: on a field change or state change, run an action (assign, notify, create task, call webhook). | S |
| FR-62 | Two-way integration with a SOAR platform: launch playbooks from a case and write results back. | S |
| FR-63 | Response actions with human confirmation (isolate host, disable account, block IOC) through connectors. | C |

### 2.8 Collaboration and communications

| ID | Requirement | Pri |
|---|---|---|
| FR-70 | Each case has its own comment stream, plus optional integration with Slack or Teams war rooms. | M |
| FR-71 | Out-of-band communication option for when corporate systems may be compromised, e.g. a standalone deployment or a separate identity provider. | S |
| FR-72 | Stakeholder notification templates for executive updates, legal, PR, and customers, with audit of what was sent and when. | S |
| FR-73 | Guest or external access for IR retainer firms and outside counsel, scoped to specific cases, time-limited, and protected by MFA. | S |
| FR-74 | Privilege marking ("Attorney-Client Privileged / Prepared at Direction of Counsel") on cases, notes, and reports. | M |

### 2.9 Regulatory and notification tracking

| ID | Requirement | Pri |
|---|---|---|
| FR-80 | Breach-notification clock tracking with configurable regimes: GDPR 72h, SEC 8-K Item 1.05 (4 business days after a materiality determination), HIPAA, state breach laws, NIS2, DORA, CIRCIA, and contractual obligations. | M |
| FR-81 | Workflow for documenting the materiality or reportability decision: decision maker, date, rationale. | M |
| FR-82 | Log of notifications to regulators, law enforcement, insurers, and customers, with attachments. | M |
| FR-83 | Cyber-insurance carrier notification tracking, including panel vendor engagement. | S |

### 2.10 Reporting and metrics

| ID | Requirement | Pri |
|---|---|---|
| FR-90 | Dashboards: open cases by severity and status, SLA compliance, analyst workload, aging. | M |
| FR-91 | Metrics: MTTD, MTTA, MTTC, MTTR, dwell time, false-positive rate, case volume by category or ATT&CK technique, and recurrence. | M |
| FR-92 | Generate reports from templates (executive summary, technical report, post-incident review) to PDF and DOCX. | M |
| FR-93 | Post-incident review module: root cause, contributing factors, what went well and what didn't, and action items tracked to closure. | M |
| FR-94 | Saved searches and full-text search across cases, notes, observables, and attachment metadata. | M |
| FR-95 | Export data through the API, CSV, or a BI connector. | S |

### 2.11 Administration

| ID | Requirement | Pri |
|---|---|---|
| FR-100 | Admin UI for workflows, fields, templates, SLAs, severity matrix, and notification rules, with no code changes needed. | M |
| FR-101 | Multi-tenancy or business-unit separation, for MSSPs or for subsidiaries with data segregation. | C |
| FR-102 | Data retention policies by case type, with automated purge that respects legal hold. | M |

---

## 3. Security requirements

| ID | Requirement | Pri |
|---|---|---|
| SR-01 | SSO through SAML 2.0 or OIDC, with MFA enforced. Local break-glass accounts are vaulted and monitored. | M |
| SR-02 | RBAC with granular permissions, plus case-level ACLs for restricted cases (insider threat, executive compromise, HR or legal matters) that are hidden even from search results. | M |
| SR-03 | Append-only, tamper-evident audit log of all create, read, update, and delete actions, including views of restricted cases. Logs are exportable to the SIEM. | M |
| SR-04 | Encryption in transit (TLS 1.2+) and at rest (AES-256). Customer-managed keys (BYOK/HSM) are optional. | M |
| SR-05 | SCIM provisioning and de-provisioning, and periodic access reviews. | S |
| SR-06 | Secure API: OAuth2 or scoped API tokens, rate limiting, and token rotation. | M |
| SR-07 | Isolation: the platform must stay usable if the primary AD or IdP is compromised (alternate authentication path). | S |
| SR-08 | Secure SDLC, annual third-party pentest, SBOM available, vulnerability disclosure program. | M |
| SR-09 | Session controls: idle timeout, concurrent session limits, IP allow-listing. | S |
| SR-10 | Data residency options (US, EU) for regulated customers. | S |

---

## 4. Non-functional requirements

| ID | Requirement | Pri |
|---|---|---|
| NFR-01 | **Availability:** 99.9% uptime, and higher during declared major incidents. Documented RTO ≤ 4h and RPO ≤ 15 min. | M |
| NFR-02 | **Performance:** case views load in under 2s at p95, and search returns in under 3s across 1M+ cases and 100M observables. | M |
| NFR-03 | **Scalability:** 500 concurrent users; ingest 10k alerts/hour without loss (queueing with back-pressure). | S |
| NFR-04 | **Usability:** keyboard shortcuts, dark mode, WCAG 2.1 AA, and a mobile-responsive view for incident commanders. | S |
| NFR-05 | **Deployment:** SaaS and/or self-hosted (containerized, air-gap capable). | S |
| NFR-06 | **Backup:** encrypted, immutable backups, with restore tested quarterly. | M |
| NFR-07 | **Interoperability:** documented REST API covering 100% of UI functions, webhooks, STIX/TAXII, and OpenAPI spec. | M |
| NFR-08 | **Compliance:** SOC 2 Type II and ISO 27001; FedRAMP Moderate if serving US federal customers. | S |
| NFR-09 | **Time:** all timestamps stored in UTC and displayed in the user's timezone, with an NTP-synced server clock. | M |

---

## 5. Integrations (minimum set)

- **Detection:** SIEM (Splunk, Sentinel, Elastic, QRadar), EDR/XDR (CrowdStrike, Defender, SentinelOne), email security.
- **Orchestration:** SOAR (XSOAR, Splunk SOAR, Tines, Torq).
- **Threat intel:** MISP, OpenCTI, commercial TIPs, VirusTotal.
- **IT and business:** ServiceNow or Jira (bi-directional ticket sync), CMDB, HR system (for insider cases), Slack, Teams, email, PagerDuty or Opsgenie.
- **Identity:** Okta or Entra ID (SSO, SCIM, and user-context enrichment).

---

## 6. Acceptance criteria (examples)

1. A phishing alert from the email gateway creates a case, extracts the sender, URLs, and attachment hashes, enriches them, and assigns the case to the on-call analyst within 60 seconds.
2. An auditor can produce a complete, ordered chain-of-custody report for any evidence item, and hash verification passes.
3. A case marked "PII involved – EU" starts a 72h GDPR clock that shows on the case and on the dashboard, and an alert fires at 48h.
4. A user without the "Insider" role gets zero results when searching for terms that exist only in a restricted case.
5. A case cannot move to Closed until the PIR fields are filled in and all child tasks are resolved or waived with a reason.

---

## 7. Out of scope (initial release)

- Native SIEM or log storage
- Autonomous containment without human approval
- Full digital forensics analysis (the tool references forensic tools; it doesn't replace them)

---

## 8. Open questions to settle with stakeholders

- SaaS or self-hosted? This depends heavily on whether case data must stay out of reach of an attacker who compromises the corporate network.
- Is the organization an MSSP (needs multi-tenancy) or a single enterprise?
- Which regulatory regimes apply (SEC, HIPAA, GDPR, NIS2, DORA, sector-specific)?
- Build or buy? Evaluate against TheHive, IRIS (open source), ServiceNow SecOps, XSOAR, Swimlane, and others using this list as the scoring rubric.

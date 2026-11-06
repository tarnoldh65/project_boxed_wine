# project_boxed_wine

A cyber incident response case management tool. See `PLAN.md` for scope and status.

The app is one workspace of frames that move and resize on a grid, plus an admin Settings page. Layouts are saved in the browser per user and tenant; Frames > Reset layout restores the default.

## Local development

Requires Docker, `uv`, and Node.js.

```sh
docker compose -f deploy/compose.yaml up --build -d --wait
```

| Service | URL |
|---|---|
| App | http://localhost:8080 |
| API | http://localhost:8000 |
| Keycloak | http://localhost:8180 (console admin/admin) |
| Mailpit | http://localhost:8025 |
| GreenMail (intake mailbox) | SMTP localhost:3025, IMAP localhost:3143 |

Dev sign-in accounts (realm `casemgr`; each enrolls a TOTP app on first sign-in):

| User | Access |
|---|---|
| admin/admin | platform admin; admin in both tenants |
| analyst/analyst | analyst in `detect-respond` |

Access comes from IdP groups named `casemgr-<tenant>-<role>` (or `casemgr-platform_admin`).

Alerts arrive through `POST /api/tenants/<tenant>/alerts` or `POST /api/tenants/<tenant>/webhooks/<source>?template=<key>`, using an API token with the `alerts:write` scope. Mail sent to `intake@example.test` becomes a case when the intake job runs:

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs email_intake
```

Evidence files upload from the browser straight to the dev S3 store (SeaweedFS, localhost:8333). The `evidence_hash` job hashes new uploads:

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs evidence_hash
```

The `sla` job fires SLA escalations and sends pending notifications (delivered to Mailpit in dev):

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs sla
```

The `clocks` job does the same for breach-notification deadlines (regimes are set per tenant in Settings):

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs clocks
```

The `retention` job purges closed cases whose type's retention period (Settings, case templates) has passed, skipping cases under legal hold:

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs retention
```

After changing API routes or models, run the backend tests (they regenerate `backend/openapi.json`), then `npm run gen:api` in `frontend/`.

## Kubernetes

Production deployment uses the Helm charts in `deploy/helm/`; see `docs/deployment.md` (secure deployment) and `docs/operations.md` (backups, restore, key rotation, load testing). To run the charts locally on kind (requires `kind` and `helm`; stop the compose stack first, since they share ports):

```sh
bash deploy/kind/up.sh              # app at https://localhost:8443
bash deploy/kind/restore-drill.sh   # restore the database from backup into a new namespace
kind delete cluster --name casemgr
```

## Tests

```sh
cd backend && uv run pytest
cd frontend && npm test
cd e2e && npx playwright test   # requires the compose stack
cd e2e && E2E_TARGET=kind BASE_URL=https://localhost:8443 npx playwright test   # against kind
```

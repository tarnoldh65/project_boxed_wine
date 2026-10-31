# project_boxed_wine

A cyber incident response case management tool. See `PLAN.md` for scope and status.

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

The `sla` job fires SLA escalations and sends pending notifications (delivered to Mailpit in dev):

```sh
docker compose -f deploy/compose.yaml exec backend python -m app.jobs sla
```

After changing API routes or models, run the backend tests (they regenerate `backend/openapi.json`), then `npm run gen:api` in `frontend/`.

## Tests

```sh
cd backend && uv run pytest
cd frontend && npm test
cd e2e && npx playwright test   # requires the compose stack
```

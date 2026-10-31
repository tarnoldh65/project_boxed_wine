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
| Keycloak | http://localhost:8180 (admin/admin; dev user analyst/analyst) |
| Mailpit | http://localhost:8025 |

## Tests

```sh
cd backend && uv run pytest
cd frontend && npm test
cd e2e && npx playwright test   # requires the compose stack
```

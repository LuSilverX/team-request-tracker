# Multi-Tenant Team Request Tracker

A working SaaS backend built with **Django REST Framework, PostgreSQL, Celery, Redis, and Docker**. Teams submit, assign, and track requests. CSV imports run asynchronously with duplicate-safe execution, bounded retries, durable job status, and audit history.

## Run locally

Requires Docker with Compose and Python 3 to generate configuration.

```sh
python3 scripts/init_env.py
docker compose up -d --build --wait
```

- Interactive API: http://localhost:8010/api/docs/
- OpenAPI schema: http://localhost:8010/api/schema/
- Health: http://localhost:8010/health/

Configuration is generated in `.env` with random secrets and owner-only file permissions. PostgreSQL and Redis are internal to the Compose network; the API binds to localhost. Migrations complete before the API, worker, and scheduler start. No credentials or demo accounts are hardcoded.

## First request

Register through `POST /api/auth/register/`:

```json
{
  "username": "alex",
  "email": "alex@example.com",
  "password": "Use-a-unique-long-password!",
  "team_name": "Operations"
}
```

Save the returned team `id`. Registration creates its owner membership. Obtain a JWT with `POST /api/auth/token/` using `username` and `password`. In Swagger, click **Authorize** and enter the access token. Send `Authorization: Bearer <access>` on authenticated API calls.

Create a request with `POST /api/teams/{team_id}/requests/`:

```json
{"title": "Review onboarding", "description": "Check the welcome email", "priority": "high"}
```

List team memberships to find assignee membership IDs. An owner or manager assigns a request with `PATCH /api/teams/{team_id}/requests/{id}/` and `{"assignee": 2}`. Membership IDs, not global user IDs, represent assignees.

## API

| Endpoint | Methods | Behavior |
| --- | --- | --- |
| `/api/auth/register/` | POST | Account and initial owner team |
| `/api/auth/token/` | POST | Access and refresh JWTs |
| `/api/auth/refresh/` | POST | Rotate refresh token; blacklist the previous one |
| `/api/auth/logout/` | POST | Blacklist supplied refresh token |
| `/api/teams/` | GET, POST | Own teams; create another team |
| `/api/teams/{team}/` | GET | Team details |
| `/api/teams/{team}/members/` | GET, POST | List members; owner adds existing user by user ID |
| `/api/teams/{team}/members/{id}/` | GET, PATCH | Read membership; owner changes role |
| `/api/teams/{team}/requests/` | GET, POST | List or submit requests |
| `/api/teams/{team}/requests/{id}/` | GET, PATCH, DELETE | Read, update, assign, delete |
| `/api/teams/{team}/imports/` | GET, POST | List jobs; upload multipart CSV |
| `/api/teams/{team}/imports/{id}/` | GET | Durable status and counts |
| `/api/teams/{team}/audit/` | GET | Read audit history |
| `/api/teams/{team}/audit/{id}/` | GET | Read an audit event |

Lists are paginated, 50 items per page (`?page=2`). Requests support `?status=open`, `?priority=high`, and `?assignee=2`. Status values are `open`, `in_progress`, and `done`; priority values are `low`, `normal`, and `high`. JWT access tokens expire after 15 minutes; refresh tokens after one day. Logout revokes the refresh token; an existing access token remains valid until expiry.

## Permissions

| Action | Member | Manager | Owner |
| --- | --- | --- | --- |
| Read team requests, members, jobs, audit | Yes | Yes | Yes |
| Submit requests | Yes | Yes | Yes |
| Edit own request contents/status | Yes | Yes | Yes |
| Change assigned request status | Yes | Yes | Yes |
| Edit any request / assign / delete | No | Yes | Yes |
| Import CSV | No | Yes | Yes |
| Add members / change roles | No | No | Yes |

All team members share visibility inside their team. Team membership is checked for every tenant route; outsiders receive 404. Nested resource lookups are scoped to that team. A member assigned someone else's request may change only its status. Owners cannot demote the last owner. Membership identities cannot be reassigned; membership deletion and invitations are not part of this version. Existing user IDs are exchanged out of band; there is no global user directory.

## CSV imports

Upload `docs/example.csv` to the imports endpoint with multipart fields `file` and `idempotency_key`. The response is HTTP 202 with a job ID; poll its status endpoint. Maximum size: 1 MiB, 1,000 rows, UTF-8.

Required headers: `external_id,title`. Optional headers: `description,priority,assignee_username`. Omit `priority` to default to `normal`; when present it must contain a valid value. Assignees must already belong to the team.

- Repeating a team/idempotency key with identical bytes returns the same job (200). Different content returns 409.
- `(team, external_id)` is unique. Existing requests are skipped, never overwritten by imports.
- Duplicate external IDs within one file are validation errors.
- A validation failure rolls back the entire file, including row audit events.
- A PostgreSQL session advisory lock prevents simultaneous execution of one job.
- Attempt counts persist before processing: at most four processing attempts, with exponential retry delays. Invalid CSV fails immediately.
- Requests, row audits, completion audit, and successful job status commit together.
- A scheduler revisits due nonterminal jobs every 30 seconds, including jobs whose initial broker publication failed. A six-minute lease provides crash recovery after the five-minute task time limit. This requires a healthy database, Redis, worker, and one scheduler.
- CSV payloads are removed on successful processing or validation failure. Unexpected failures retain the payload for diagnosis; define a retention policy before handling real customer data.
- Only user-visible error summaries appear on job endpoints; internal failures are logged server-side.

## Verify

```sh
docker compose exec -T web pytest -q
docker compose exec -T web ruff check .
docker compose exec -T web ruff format --check .
docker compose exec -T web python manage.py makemigrations --check --dry-run
docker compose exec -T web python manage.py spectacular --file /tmp/openapi.yaml --validate --fail-on-warn
python3 scripts/smoke.py
sh scripts/backup.sh
sh scripts/verify_restore.sh backups/<backup>.dump
sh scripts/verify_rollback.sh backups/<backup>.dump
```

Tests use PostgreSQL, including concurrent execution and database constraints. The smoke test uses HTTP and the real Redis/Celery worker and creates an isolated team. CI repeats the full container startup, tests, smoke test, backup restoration, and reversible migration drill. See [verification](docs/VERIFICATION.md) for the execution results from this workspace and [operations](docs/OPERATIONS.md) for deployment and rollback instructions.

## Architecture and boundaries

This is a backend; Swagger is the interactive interface. Tenant isolation is enforced by API query scoping, immutable ownership fields, membership checks, and composite database foreign keys. It does **not** implement PostgreSQL row-level security: trusted application and operator access can read across teams. Audit events are written atomically with API mutations, exposed read-only, and protected by a PostgreSQL update/delete trigger. Database administrators can bypass these protections; this is not a cryptographically tamper-evident log.

Team-level write locks serialize role changes and request mutations. This keeps authorization changes consistent and simplifies imports; very high write-volume teams would need finer locking or staged imports. CSV input is bounded and stored in PostgreSQL, so no shared upload filesystem is needed. Audit events contain before/after request contents; consider those records sensitive.

The local Compose database role owns the database to support migrations and disposable test databases. For hosted production, use a separate migration role and a restricted runtime role, TLS at the edge, backups stored off-host, monitoring, and appropriate authentication/registration policies. Shared Redis throttling is abuse friction, not a strict security rate limit; it fails open during cache outages so authenticated API work and durable import queuing remain available. Use an edge rate limiter for strict protection. The `/health/` endpoint checks the database; worker and scheduler monitoring are separate.

## Design references

- [Django 5.2 release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/) — supported Python version and LTS baseline.
- [Celery task guide](https://docs.celeryq.dev/en/v5.5.0/userguide/tasks.html) — retry and acknowledgement behavior. Durable database state supplies the project's stricter duplicate and retry guarantees.

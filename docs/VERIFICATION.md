# Verification record

Verified in this workspace on 2026-10-01 UTC (2026-09-30 America/Los_Angeles).

| Check | Result |
| --- | --- |
| Docker image build and Compose startup | Passed; API healthy at localhost:8010 |
| PostgreSQL-backed automated suite | **34 passed in 41.73 seconds**, no warnings |
| Code lint | Passed |
| Formatting | 27 Python files passed |
| Migration/model consistency | No changes detected |
| OpenAPI generation and validation, warnings treated as errors | Passed; exported to `docs/openapi.yaml` |
| Real HTTP/JWT/Redis/Celery smoke test | Passed on initial and final image |
| PostgreSQL backup | Custom-format dump created successfully |
| Restore into a disposable database | Passed; migrations current, tenant references and audit trigger valid |
| Source versus restored row counts at backup time | Both: 1 team, 1 membership, 2 requests, 1 import job, 5 audit events |
| Migration rollback/reapply on restored database | `0002` reversed to `0001`, reapplied, data checks passed |

Tests cover cross-team collection/detail reads, edits/deletes, scoped assignments, database reference constraints, forged ownership fields, role boundaries, last-owner protection, immutable membership identities, JWT authentication/logout, read-only and database-protected audit records, input limits, duplicate upload keys, duplicate execution, parallel delivery, per-team external IDs, atomic validation failures, persistent retry bounds, permission revocation, scheduled recovery, rollback after mid-import failure, and cache/broker outage handling.

The smoke test creates isolated test teams and leaves them in the local database. It exercises registration, login, request creation, CSV upload, real background completion, duplicate upload behavior, and audit history. No real customer data was imported.

## Scope of the evidence

- The running service is a local Docker deployment. No public cloud deployment or remote GitHub Actions run has been performed.
- CI is configured to repeat build, tests, schema checks, live smoke, backup restoration, and rollback drills once the repository is pushed to GitHub.
- Restore and rollback drills used disposable databases, which were cleaned up afterward. The running database was not overwritten.
- The rollback drill verifies the current guard migration only. It does not establish rollback safety for future schema changes or prove application-image rollback across two released versions.
- Process-crash retry bounds are tested through persisted crash state; the suite does not simulate host loss or claim a chaos/load/security audit.
- Container base tags are version-family tags. Python dependencies are pinned in `requirements.txt`; pin release image digests in a production release process.

## Documentation-page fix

A subsequent browser check exposed missing Django template configuration: the schema was valid, but `/api/docs/` returned HTTP 500. Added the Django template backend with app template discovery, rebuilt the containers, and verified that Swagger renders its endpoint list in the browser. The new documentation-page regression test passes independently of the original 34-test run.

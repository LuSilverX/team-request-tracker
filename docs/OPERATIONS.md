# Deployment, backup, and rollback

## Local deployment

`python3 scripts/init_env.py` creates unique local secrets. `docker compose up -d --build --wait` builds the image, starts PostgreSQL/Redis, applies migrations through a one-shot service, then starts Gunicorn, Celery, and one Celery Beat scheduler. Use `docker compose ps` and `docker compose logs --tail=100 web worker beat` to inspect it. `docker compose stop` stops without deleting data. Do not use `down -v` on data you want to keep.

This repository has been deployed locally in Docker. A public deployment requires a chosen host/domain and credentials; no public cloud resources are created by these scripts.

## Hosted release procedure

1. Run the CI workflow, including restore and migration reversal tests. Tag the resulting image with an immutable release identifier and publish it to your registry.
2. Configure a production `.env`: unique secrets, explicit allowed hosts, `DJANGO_DEBUG=false`, PostgreSQL/Redis addresses and credentials, and `CACHE_URL` for a shared cache. The supplied Compose file deliberately overrides database/broker hosts for its local network; adapt it when using managed services.
3. Put the localhost-bound API behind a TLS reverse proxy. Enable `DJANGO_SSL_REDIRECT=true` and `DJANGO_HSTS_SECONDS=31536000` once HTTPS is configured. If TLS terminates at the proxy, configure Django's trusted proxy HTTPS header only after the proxy is known to strip client-supplied forwarding headers. Run `python manage.py check --deploy` with those real settings. The local health probe uses HTTP; adjust it for the chosen TLS topology.
4. Give the runtime database role only required table/sequence privileges; reserve schema changes, restore privileges, and database creation for a separate operator/migration role. Restrict network access to PostgreSQL and Redis. Use authenticated/TLS connections for managed services.
5. Take and verify a backup. Pause ingress and stop worker/beat before schema-changing releases. Run the new image's migration job once. Start web/worker/beat with the new image, check health, and run a smoke test. Keep the previous image and release configuration.
6. Monitor request failures/latency, queue depth, oldest queued/running jobs, failed imports, worker availability, scheduler heartbeat, disk usage, and backup age. Schedule refresh-token cleanup with `python manage.py flushexpiredtokens`. Define CSV/audit retention and account lifecycle policies.

The repository's CI automates verification and the local deployment path. Registry publishing and host-specific continuous deployment need your deployment destination. Production resilience, external monitoring, TLS, and point-in-time recovery are not supplied by the single-host Compose environment.

## Backup and restoration drill

```sh
sh scripts/backup.sh
sh scripts/verify_restore.sh backups/tracker-YYYYMMDDTHHMMSSZ.dump
```

The custom-format PostgreSQL dump contains application tables, auth state, migration history, tenant constraints, and audit protection. The verification script restores to a uniquely named disposable database, checks migrations and data invariants, reports row counts, then drops only that disposable database. It never replaces `tracker`. Compare the reported counts with the source snapshot when collecting release evidence. Keep encrypted copies off-host with a tested retention policy; local dumps alone do not survive host loss. Backups may contain personal data and password hashes.

For an actual incident, stop ingress/web/worker/beat, restore the selected dump into a **new** database, validate it, then configure all application processes to use that database. Keep the original database until validation is complete. Restore only trusted PostgreSQL dumps. Reconcile work after the snapshot before reopening traffic. The durable ImportJob records can republish unfinished work without a Redis backup. Completed jobs and external-ID constraints make stale queued messages safe to redeliver.

## Rollback

Prefer rolling the application back to the previous immutable image while retaining a backward-compatible schema. Use expand/contract migrations so old and new releases can coexist during a release window.

A tested schema rollback drill is included:

```sh
sh scripts/verify_rollback.sh backups/tracker-YYYYMMDDTHHMMSSZ.dump
```

It restores a disposable database, reverses `tracker.0002_tenant_guards` to `0001`, reapplies all migrations, and verifies the data invariants and audit trigger. Reversing this migration removes protections temporarily, so the drill is isolated. It proves that this specific migration is reversible; it does not prove an arbitrary future release can be rolled back safely.

For an incompatible live migration, stop all writers, review the migration's reverse operations and expected data loss, test the exact sequence against a restored copy, then either execute the approved reversal or switch to a verified pre-release restore. Never blindly migrate the live application to zero. A database restore loses writes since the snapshot; recovery objectives must be chosen for the deployment.

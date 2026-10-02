import csv
import io
import logging
import uuid
from datetime import timedelta

from celery import shared_task
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import ImportJob, Membership, Team, WorkRequest
from .services import audit, snapshot

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 4


class InvalidCSV(Exception):
    pass


def enqueue(job_id):
    try:
        process_import.delay(job_id)
    except Exception:
        logger.exception("Import dispatch deferred to scheduled recovery: %s", job_id)


@shared_task
def dispatch_imports():
    now = timezone.now()
    jobs = (
        ImportJob.objects.filter(status__in=["queued", "running", "retrying"])
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by("next_attempt_at", "created_at")
        .values_list("id", flat=True)[:100]
    )
    for job_id in jobs:
        enqueue(str(job_id))


def parse_csv(payload):
    reader = csv.DictReader(io.StringIO(payload, newline=""), strict=True)
    required = {"external_id", "title"}
    allowed = required | {"description", "priority", "assignee_username"}
    if (
        not reader.fieldnames
        or not required.issubset(reader.fieldnames)
        or set(reader.fieldnames) - allowed
        or len(reader.fieldnames) != len(set(reader.fieldnames))
    ):
        raise InvalidCSV("Headers must include external_id,title; optional: description,priority,assignee_username.")
    rows = []
    seen = set()
    for number, row in enumerate(reader, 2):
        if number > 1001:
            raise InvalidCSV("At most 1,000 rows are allowed.")
        if None in row or any(value is None for value in row.values()):
            raise InvalidCSV(f"Row {number}: column count does not match headers.")
        row = {key: value.strip() for key, value in row.items()}
        for key, limit in (("external_id", 100), ("title", 200), ("description", 10000), ("assignee_username", 150)):
            value = row.get(key, "")
            if (key in required and not value) or len(value) > limit or "\x00" in value:
                raise InvalidCSV(f"Row {number}: invalid {key}.")
        if row.get("priority", "normal") not in WorkRequest.Priority.values:
            raise InvalidCSV(f"Row {number}: invalid priority.")
        if row["external_id"] in seen:
            raise InvalidCSV(f"Row {number}: duplicate external_id within file.")
        seen.add(row["external_id"])
        rows.append(row)
    if not rows:
        raise InvalidCSV("CSV must contain at least one data row.")
    return rows


@shared_task(bind=True, max_retries=3)
def process_import(self, job_id):
    # A session advisory lock spans the durable attempt update and the atomic import.
    # It is released automatically when a crashed worker's DB connection closes.
    lock_id = uuid.UUID(str(job_id)).int & ((1 << 63) - 1)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        if not cursor.fetchone()[0]:
            return
    try:
        job = ImportJob.objects.filter(pk=job_id).first()
        if job is None or job.status in ("succeeded", "failed"):
            return
        if job.next_attempt_at and job.next_attempt_at > timezone.now():
            return
        if job.attempts >= MAX_ATTEMPTS:
            ImportJob.objects.filter(pk=job_id).update(
                status="failed", error="Import exhausted its four allowed attempts.", updated_at=timezone.now()
            )
            return
        job.attempts += 1
        job.status = "running"
        job.next_attempt_at = timezone.now() + timedelta(minutes=6)
        job.save(update_fields=["attempts", "status", "next_attempt_at", "updated_at"])
        try:
            rows = parse_csv(job.payload)
            with transaction.atomic():
                Team.objects.select_for_update().get(pk=job.team_id)
                submitter = Membership.objects.select_related("user").get(pk=job.submitted_by_id)
                if submitter.role not in ("owner", "manager"):
                    raise InvalidCSV("Submitter no longer has import permission.")
                members = {
                    m.user.username: m for m in Membership.objects.filter(team_id=job.team_id).select_related("user")
                }
                created = skipped = 0
                for row in rows:
                    username = row.get("assignee_username", "")
                    if username and username not in members:
                        raise InvalidCSV("Assignee must be an existing member of this team.")
                    obj, is_new = WorkRequest.objects.get_or_create(
                        team_id=job.team_id,
                        external_id=row["external_id"],
                        defaults={
                            "title": row["title"],
                            "description": row.get("description", ""),
                            "priority": row.get("priority", "normal"),
                            "created_by": submitter,
                            "assignee": members.get(username),
                        },
                    )
                    if is_new:
                        created += 1
                        audit(job.team, submitter.user, "request.imported", obj, snapshot(obj))
                    else:
                        skipped += 1
                job.status = "succeeded"
                job.created_count = created
                job.skipped_count = skipped
                job.error = ""
                job.next_attempt_at = None
                job.payload = ""
                job.save()
                audit(job.team, submitter.user, "import.succeeded", job, {"created": created, "skipped": skipped})
        except (InvalidCSV, csv.Error) as exc:
            ImportJob.objects.filter(pk=job_id).update(
                status="failed", error=str(exc)[:500], payload="", next_attempt_at=None, updated_at=timezone.now()
            )
        except Exception:
            logger.exception("Import attempt failed: %s", job_id)
            exhausted = job.attempts >= MAX_ATTEMPTS
            delay = min(30 * 2 ** (job.attempts - 1), 240)
            ImportJob.objects.filter(pk=job_id).update(
                status="failed" if exhausted else "retrying",
                error="Import failed after four attempts."
                if exhausted
                else "Temporary processing failure; retry scheduled.",
                next_attempt_at=None if exhausted else timezone.now() + timedelta(seconds=delay),
                updated_at=timezone.now(),
            )
            if not exhausted:
                # Beat is the fallback if publishing this retry fails or its message is lost.
                raise self.retry(countdown=delay)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])

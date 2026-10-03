from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.db import close_old_connections
from django.utils import timezone

from tracker.models import AuditLog, ImportJob, WorkRequest
from tracker.tasks import dispatch_imports, process_import


@pytest.fixture
def job(world):
    return ImportJob.objects.create(
        team=world["team"],
        submitted_by=world["members"]["manager"],
        idempotency_key="test",
        content_hash="a" * 64,
        payload="external_id,title,assignee_username\nA,First,member\nB,Second,peer\n",
    )


@pytest.mark.django_db(transaction=True)
def test_duplicate_execution_and_external_ids(job):
    process_import.run(str(job.pk))
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "succeeded" and job.attempts == 1 and job.created_count == 2
    assert AuditLog.objects.filter(action="request.imported").count() == 2
    other = ImportJob.objects.create(
        team=job.team,
        submitted_by=job.submitted_by,
        idempotency_key="again",
        content_hash="b" * 64,
        payload="external_id,title\nA,Changed\n",
    )
    process_import.run(str(other.pk))
    other.refresh_from_db()
    assert other.skipped_count == 1
    assert WorkRequest.objects.get(external_id="A").title == "First"


@pytest.mark.django_db(transaction=True)
def test_parallel_delivery(job):
    def run():
        close_old_connections()
        try:
            process_import.run(str(job.pk))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    job.refresh_from_db()
    assert job.status == "succeeded" and job.created_count == 2 and job.attempts == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "payload",
    [
        "wrong,headers\nx,y\n",
        "external_id,title\nA,First\nA,Duplicate\n",
        "external_id,title,assignee_username\nA,Good,member\nB,Bad,outsider\n",
        "external_id,title\nA,Too,many\n",
        "external_id,title\n",
        "external_id,title,priority\nA,B,urgent\n",
    ],
)
def test_invalid_import_rolls_back(job, payload):
    job.payload = payload
    job.save()
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "failed" and job.attempts == 1
    assert not WorkRequest.objects.filter(external_id__isnull=False).exists()
    assert not AuditLog.objects.filter(action="request.imported").exists()


@pytest.mark.django_db(transaction=True)
def test_retry_budget_persists(job):
    with patch("tracker.tasks.parse_csv", side_effect=RuntimeError("internal sensitive detail")):
        for attempt in range(1, 5):
            ImportJob.objects.filter(pk=job.pk).update(next_attempt_at=None)
            if attempt < 4:
                with pytest.raises(Retry):
                    process_import.run(str(job.pk))
            else:
                process_import.run(str(job.pk))
            job.refresh_from_db()
            assert job.attempts == attempt
            assert "sensitive" not in job.error
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "failed" and job.attempts == 4


@pytest.mark.django_db(transaction=True)
def test_crashed_worker_final_attempt_is_bounded(job):
    job.attempts = 4
    job.status = "running"
    job.save()
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "failed" and job.attempts == 4


@pytest.mark.django_db(transaction=True)
def test_revoked_permission_blocks_import(job):
    member = job.submitted_by
    member.role = "member"
    member.save()
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "failed"
    assert not WorkRequest.objects.filter(external_id__isnull=False).exists()


@pytest.mark.django_db(transaction=True)
def test_recovery_dispatches_due_jobs(job):
    with patch("tracker.tasks.enqueue") as enqueue:
        dispatch_imports.run()
        enqueue.assert_called_once_with(str(job.pk))
    job.next_attempt_at = timezone.now() + timezone.timedelta(hours=1)
    job.save()
    with patch("tracker.tasks.enqueue") as enqueue:
        dispatch_imports.run()
        enqueue.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_mid_import_failure_rolls_back_then_recovers(job):
    from tracker.services import audit as real_audit

    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated database operation failure")
        return real_audit(*args, **kwargs)

    with patch("tracker.tasks.audit", side_effect=fail_second), pytest.raises(Retry):
        process_import.run(str(job.pk))
    assert not WorkRequest.objects.filter(external_id__isnull=False).exists()
    assert not AuditLog.objects.filter(action="request.imported").exists()
    job.refresh_from_db()
    assert job.status == "retrying" and job.attempts == 1
    ImportJob.objects.filter(pk=job.pk).update(next_attempt_at=None)
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.status == "succeeded" and job.attempts == 2 and job.created_count == 2


@pytest.mark.django_db(transaction=True)
def test_duplicate_external_id_is_tenant_scoped(job, world):
    WorkRequest.objects.create(
        team=world["other_team"], created_by=world["members"]["outsider"], external_id="A", title="Other team"
    )
    process_import.run(str(job.pk))
    job.refresh_from_db()
    assert job.created_count == 2
    assert WorkRequest.objects.filter(external_id="A").count() == 2

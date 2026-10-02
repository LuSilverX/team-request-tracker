import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner"
        MANAGER = "manager"
        MEMBER = "member"

    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="one_membership_per_team"),
            models.UniqueConstraint(fields=["id", "team"], name="membership_id_team"),
            models.CheckConstraint(condition=Q(role__in=["owner", "manager", "member"]), name="valid_membership_role"),
        ]


class WorkRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"
        IN_PROGRESS = "in_progress"
        DONE = "done"

    class Priority(models.TextChoices):
        LOW = "low"
        NORMAL = "normal"
        HIGH = "high"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=10000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    created_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="submitted_requests")
    assignee = models.ForeignKey(
        Membership, on_delete=models.PROTECT, related_name="assigned_requests", null=True, blank=True
    )
    external_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [models.Index(fields=["team", "status", "-created_at"])]
        constraints = [
            models.UniqueConstraint(fields=["team", "external_id"], name="unique_external_request"),
            models.CheckConstraint(
                condition=Q(status__in=["open", "in_progress", "done"]), name="valid_request_status"
            ),
            models.CheckConstraint(condition=Q(priority__in=["low", "normal", "high"]), name="valid_request_priority"),
        ]


class ImportJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        RETRYING = "retrying"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    submitted_by = models.ForeignKey(Membership, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    content_hash = models.CharField(max_length=64)
    payload = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["team", "idempotency_key"], name="unique_import_key")]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class AuditLog(models.Model):
    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    object_id = models.CharField(max_length=64)
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["team", "-created_at"])]

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from tracker.models import AuditLog, ImportJob, Membership, Team, WorkRequest


class Command(BaseCommand):
    help = "Check restored tenant references, owner invariant, audit trigger and row counts."

    def handle(self, *args, **options):
        for team in Team.objects.all():
            if not Membership.objects.filter(team=team, role="owner").exists():
                raise CommandError(f"Team {team.pk} has no owner")
        checks = [
            "SELECT count(*) FROM tracker_workrequest r JOIN tracker_membership m ON r.created_by_id=m.id WHERE r.team_id<>m.team_id",
            "SELECT count(*) FROM tracker_workrequest r JOIN tracker_membership m ON r.assignee_id=m.id WHERE r.team_id<>m.team_id",
            "SELECT count(*) FROM tracker_importjob j JOIN tracker_membership m ON j.submitted_by_id=m.id WHERE j.team_id<>m.team_id",
        ]
        with connection.cursor() as cursor:
            for sql in checks:
                cursor.execute(sql)
                if cursor.fetchone()[0]:
                    raise CommandError("Cross-tenant reference detected")
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname='audit_append_only' AND tgenabled='O'")
            if cursor.fetchone()[0] != 1:
                raise CommandError("Audit protection is missing")
        for model in (Team, Membership, WorkRequest, ImportJob, AuditLog):
            self.stdout.write(f"{model.__name__}: {model.objects.count()}")
        self.stdout.write(self.style.SUCCESS("Data invariants verified."))

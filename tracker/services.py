from .models import AuditLog


def audit(team, user, action, obj, changes):
    return AuditLog.objects.create(team=team, actor=user, action=action, object_id=str(obj.pk), changes=changes)


def snapshot(obj):
    return {
        "title": obj.title,
        "description": obj.description,
        "status": obj.status,
        "priority": obj.priority,
        "assignee": obj.assignee_id,
    }

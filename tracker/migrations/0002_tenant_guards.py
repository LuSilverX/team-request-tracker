from django.db import migrations

FORWARD = """
ALTER TABLE tracker_workrequest ADD CONSTRAINT assignee_same_team
FOREIGN KEY (assignee_id, team_id) REFERENCES tracker_membership (id, team_id);
ALTER TABLE tracker_workrequest ADD CONSTRAINT creator_same_team
FOREIGN KEY (created_by_id, team_id) REFERENCES tracker_membership (id, team_id);
ALTER TABLE tracker_importjob ADD CONSTRAINT submitter_same_team
FOREIGN KEY (submitted_by_id, team_id) REFERENCES tracker_membership (id, team_id);
CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are append-only' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON tracker_auditlog
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
"""
REVERSE = """
DROP TRIGGER audit_append_only ON tracker_auditlog;
DROP FUNCTION reject_audit_mutation();
ALTER TABLE tracker_importjob DROP CONSTRAINT submitter_same_team;
ALTER TABLE tracker_workrequest DROP CONSTRAINT creator_same_team;
ALTER TABLE tracker_workrequest DROP CONSTRAINT assignee_same_team;
"""


class Migration(migrations.Migration):
    dependencies = [("tracker", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]

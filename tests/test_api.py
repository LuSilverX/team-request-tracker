from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from tracker.models import AuditLog, ImportJob, Membership, WorkRequest


def url(world, resource="requests", obj=None, other=False):
    team = world["other_team" if other else "team"]
    return f"/api/teams/{team.pk}/{resource}/" + (f"{obj.pk}/" if obj else "")


@pytest.mark.django_db
@pytest.mark.parametrize("resource", ["requests", "members", "imports", "audit"])
def test_foreign_collections_hidden(world, client_for, resource):
    assert client_for("owner").get(url(world, resource, other=True)).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_foreign_request_hidden(world, client_for, method):
    client = client_for("owner")
    assert getattr(client, method)(url(world, obj=world["foreign"]), {"title": "attack"}).status_code == 404
    world["foreign"].refresh_from_db()
    assert world["foreign"].title == "Secret"


@pytest.mark.django_db
def test_list_is_scoped(world, client_for):
    result = client_for("owner").get(url(world)).data
    assert result["count"] == 1
    assert result["results"][0]["title"] == "Original"
    assert client_for("owner").get("/api/teams/").data["count"] == 1


@pytest.mark.django_db
def test_assignment_scope_and_database_guards(world, client_for):
    client = client_for("manager")
    assert (
        client.patch(url(world, obj=world["request"]), {"assignee": world["members"]["outsider"].pk}).status_code == 400
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkRequest.objects.filter(pk=world["request"].pk).update(assignee=world["members"]["outsider"])
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkRequest.objects.filter(pk=world["request"].pk).update(created_by=world["members"]["outsider"])


@pytest.mark.django_db
def test_member_permissions_and_audit(world, client_for):
    target = url(world, obj=world["request"])
    assert client_for("peer").patch(target, {"title": "No"}).status_code == 403
    assert client_for("member").patch(target, {"assignee": world["members"]["peer"].pk}).status_code == 403
    assert client_for("member").patch(target, {"title": "Updated"}).status_code == 200
    assert client_for("member").delete(target).status_code == 403
    assert client_for("manager").patch(target, {"assignee": world["members"]["peer"].pk}).status_code == 200
    assert client_for("peer").patch(target, {"status": "in_progress"}).status_code == 200
    assert client_for("peer").patch(target, {"title": "No"}).status_code == 403
    log = AuditLog.objects.filter(action="request.updated", changes__title__to="Updated").get()
    assert log.actor == world["users"]["member"]
    assert log.changes["title"]["from"] == "Original"
    assert client_for("manager").delete(target).status_code == 204
    assert AuditLog.objects.filter(action="request.deleted", object_id=str(world["request"].pk)).exists()


@pytest.mark.django_db
def test_cannot_forge_team_or_creator(world, client_for):
    response = client_for("member").post(
        url(world), {"title": "New", "team": str(world["other_team"].pk), "created_by": world["members"]["outsider"].pk}
    )
    assert response.status_code == 201
    obj = WorkRequest.objects.get(pk=response.data["id"])
    assert obj.team == world["team"] and obj.created_by == world["members"]["member"]


@pytest.mark.django_db
def test_owner_management_and_last_owner(world, client_for):
    endpoint = url(world, "members", world["members"]["owner"])
    assert client_for("manager").patch(endpoint, {"role": "member"}).status_code == 403
    assert client_for("owner").patch(endpoint, {"role": "member"}).status_code == 400
    peer = url(world, "members", world["members"]["peer"])
    assert client_for("owner").patch(peer, {"role": "owner"}).status_code == 200
    assert client_for("owner").patch(endpoint, {"role": "member"}).status_code == 200
    assert client_for("owner").patch(peer, {"role": "member"}).status_code == 403


@pytest.mark.django_db
def test_membership_identity_immutable(world, client_for):
    assert (
        client_for("owner")
        .patch(url(world, "members", world["members"]["peer"]), {"user": world["users"]["outsider"].pk})
        .status_code
        == 400
    )


@pytest.mark.django_db
def test_registration_auth_and_logout():
    client = APIClient()
    assert client.get("/api/teams/").status_code == 401
    data = {
        "username": "newuser",
        "email": "new@example.com",
        "password": "StrongTestingSecret!97",
        "team_name": "Fresh",
    }
    assert client.post("/api/auth/register/", data).status_code == 201
    assert Membership.objects.get(user__username="newuser").role == "owner"
    token = client.post("/api/auth/token/", {"username": data["username"], "password": data["password"]})
    assert token.status_code == 200
    client.credentials(HTTP_AUTHORIZATION="Bearer " + token.data["access"])
    assert client.get("/api/teams/").status_code == 200
    assert client.post("/api/auth/logout/", {"refresh": token.data["refresh"]}).status_code == 200
    assert client.post("/api/auth/refresh/", {"refresh": token.data["refresh"]}).status_code == 401


def upload(client, endpoint, content=b"external_id,title\nx,Example\n", key="key"):
    return client.post(
        endpoint,
        {"file": SimpleUploadedFile("requests.csv", content, content_type="text/csv"), "idempotency_key": key},
        format="multipart",
    )


@pytest.mark.django_db
def test_upload_idempotency_and_scope(world, client_for):
    endpoint = url(world, "imports")
    client = client_for("manager")
    with patch("tracker.tasks.enqueue"):
        first = upload(client, endpoint)
        assert first.status_code == 202
        same = upload(client, endpoint)
        assert same.status_code == 200 and same.data["id"] == first.data["id"]
        assert upload(client, endpoint, b"external_id,title\ny,Other\n").status_code == 409
    assert upload(client_for("member"), endpoint).status_code == 403
    assert upload(client_for("outsider"), endpoint).status_code == 404
    assert client_for("outsider").get(endpoint + first.data["id"] + "/").status_code == 404
    assert ImportJob.objects.count() == 1


@pytest.mark.django_db
def test_upload_validation(world, client_for):
    client = client_for("owner")
    assert upload(client, url(world, "imports"), b"\xff").status_code == 400
    assert upload(client, url(world, "imports"), b"x" * (1024 * 1024 + 1)).status_code == 400


@pytest.mark.django_db
def test_audit_read_only(world, client_for):
    client = client_for("owner")
    assert client.post(url(world, "audit"), {}).status_code == 405
    client.post(url(world), {"title": "Audited"})
    log = AuditLog.objects.first()
    assert client.patch(url(world, "audit", log), {"changes": {}}).status_code == 405
    assert client.delete(url(world, "audit", log)).status_code == 405
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditLog.objects.filter(pk=log.pk).update(action="tampered")


@pytest.mark.django_db
def test_broker_outage_leaves_recoverable_job(world, client_for, django_capture_on_commit_callbacks):
    with patch("tracker.tasks.process_import.delay", side_effect=ConnectionError("broker offline")):
        with django_capture_on_commit_callbacks(execute=True):
            response = upload(client_for("owner"), url(world, "imports"))
    assert response.status_code == 202
    assert ImportJob.objects.get(pk=response.data["id"]).status == "queued"


@pytest.mark.django_db
def test_invalid_request_id_is_client_error(world, client_for):
    assert client_for("owner").patch(url(world) + "invalid/", {"title": "bad"}).status_code == 404


@pytest.mark.django_db
def test_cache_outage_does_not_prevent_durable_upload(world, client_for, django_capture_on_commit_callbacks):
    from redis.exceptions import ConnectionError as RedisConnectionError

    with patch("django.core.cache.cache.get", side_effect=RedisConnectionError("cache unavailable")):
        with patch("tracker.tasks.process_import.delay", side_effect=RedisConnectionError("broker unavailable")):
            with django_capture_on_commit_callbacks(execute=True):
                response = upload(client_for("owner"), url(world, "imports"))
    assert response.status_code == 202
    assert ImportJob.objects.get(pk=response.data["id"]).status == "queued"

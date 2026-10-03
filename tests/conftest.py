import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tracker.models import Membership, Team, WorkRequest


@pytest.fixture
def world(db):
    users = {
        name: get_user_model().objects.create_user(username=name, password="ExcellentTestPass!42")
        for name in ["owner", "manager", "member", "peer", "outsider"]
    }
    alpha, beta = Team.objects.create(name="Alpha"), Team.objects.create(name="Beta")
    memberships = {
        name: Membership.objects.create(
            team=beta if name == "outsider" else alpha,
            user=user,
            role=name if name in ("owner", "manager") else "member",
        )
        for name, user in users.items()
    }
    request = WorkRequest.objects.create(team=alpha, title="Original", created_by=memberships["member"])
    foreign = WorkRequest.objects.create(team=beta, title="Secret", created_by=memberships["outsider"])
    return {
        "users": users,
        "members": memberships,
        "team": alpha,
        "other_team": beta,
        "request": request,
        "foreign": foreign,
    }


@pytest.fixture
def client_for(world):
    def client(name):
        api = APIClient()
        api.force_authenticate(world["users"][name])
        return api

    return client

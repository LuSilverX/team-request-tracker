from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import AuditLog, ImportJob, Membership, Team, WorkRequest


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    team_name = serializers.CharField(max_length=120)

    def validate_username(self, value):
        get_user_model()._meta.get_field("username").run_validators(value)
        if get_user_model().objects.filter(username=value).exists():
            raise serializers.ValidationError("Username unavailable.")
        return value

    def validate(self, attrs):
        validate_password(attrs["password"], get_user_model()(username=attrs["username"], email=attrs["email"]))
        return attrs


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "created_at"]
        read_only_fields = ["id", "created_at"]


class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "role"]
        read_only_fields = ["id", "username"]


class RequestSerializer(serializers.ModelSerializer):
    assignee = serializers.PrimaryKeyRelatedField(queryset=Membership.objects.none(), allow_null=True, required=False)

    class Meta:
        model = WorkRequest
        fields = [
            "id",
            "team",
            "title",
            "description",
            "status",
            "priority",
            "created_by",
            "assignee",
            "external_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "team", "created_by", "external_id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        team_id = self.context.get("team_id")
        if team_id:
            self.fields["assignee"].queryset = Membership.objects.filter(team_id=team_id)


class ImportUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    idempotency_key = serializers.CharField(max_length=128)


class ImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJob
        fields = [
            "id",
            "team",
            "submitted_by",
            "status",
            "attempts",
            "created_count",
            "skipped_count",
            "error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "actor", "action", "object_id", "changes", "created_at"]
        read_only_fields = fields

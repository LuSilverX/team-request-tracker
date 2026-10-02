import hashlib

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import AuditLog, ImportJob, Membership, Team, WorkRequest
from .serializers import (
    AuditSerializer,
    ImportSerializer,
    ImportUploadSerializer,
    MembershipSerializer,
    RegistrationSerializer,
    RequestSerializer,
    TeamSerializer,
)
from .services import audit, snapshot
from .throttles import AuthenticationThrottle


class LoginView(TokenObtainPairView):
    throttle_classes = [AuthenticationThrottle]
    throttle_scope = "auth"


class RefreshView(TokenRefreshView):
    throttle_classes = [AuthenticationThrottle]
    throttle_scope = "auth"


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthenticationThrottle]
    throttle_scope = "auth"

    @extend_schema(request=RegistrationSerializer, responses={201: TeamSerializer})
    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                user = get_user_model().objects.create_user(
                    username=data["username"], email=data["email"], password=data["password"]
                )
                team = Team.objects.create(name=data["team_name"])
                Membership.objects.create(team=team, user=user, role="owner")
                audit(team, user, "team.created", team, {"name": team.name})
        except IntegrityError:
            raise ValidationError({"username": "Username unavailable."})
        return Response(TeamSerializer(team).data, status=201)


class TeamViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = TeamSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Team.objects.none()
        return Team.objects.filter(membership__user=self.request.user).order_by("created_at", "id")

    @transaction.atomic
    def perform_create(self, serializer):
        team = serializer.save()
        Membership.objects.create(team=team, user=self.request.user, role="owner")
        audit(team, self.request.user, "team.created", team, {"name": team.name})


class TenantMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.membership = (
            Membership.objects.select_related("team").filter(team_id=kwargs["team_id"], user=request.user).first()
        )
        if self.membership is None:
            raise NotFound()

    def require_manager(self):
        if self.membership.role not in ("owner", "manager"):
            raise PermissionDenied("An owner or manager is required.")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["team_id"] = self.kwargs.get("team_id")
        return context


class MembershipViewSet(
    TenantMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MembershipSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Membership.objects.filter(team_id=self.kwargs.get("team_id")).select_related("user").order_by("id")

    def lock_owner(self):
        # Serialize role changes and request writes on the team to prevent stale authorization.
        team = Team.objects.select_for_update().get(pk=self.membership.team_id)
        if Membership.objects.get(pk=self.membership.pk).role != "owner":
            raise PermissionDenied("Only owners can manage memberships.")
        return team

    @transaction.atomic
    def perform_create(self, serializer):
        team = self.lock_owner()
        try:
            with transaction.atomic():
                member = serializer.save(team=team)
        except IntegrityError:
            raise ValidationError("User is already a member of this team.")
        audit(team, self.request.user, "membership.created", member, {"user": member.user_id, "role": member.role})

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        team = self.lock_owner()
        member = self.get_object()
        if "user" in request.data:
            raise ValidationError("Membership identity cannot be changed.")
        serializer = self.get_serializer(member, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_role = serializer.validated_data.get("role", member.role)
        if (
            member.role == "owner"
            and new_role != "owner"
            and not Membership.objects.filter(team=team, role="owner").exclude(pk=member.pk).exists()
        ):
            raise ValidationError("A team must retain at least one owner.")
        previous = member.role
        serializer.save()
        audit(team, request.user, "membership.updated", member, {"role": {"from": previous, "to": member.role}})
        return Response(serializer.data)


class RequestViewSet(TenantMixin, viewsets.ModelViewSet):
    serializer_class = RequestSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = WorkRequest.objects.filter(team_id=self.kwargs.get("team_id"))
        for field in ("status", "priority", "assignee"):
            value = self.request.query_params.get(field)
            if value:
                if field == "assignee" and not value.isdecimal():
                    raise ValidationError({field: "Expected a membership ID."})
                qs = qs.filter(**{field: value})
        return qs

    def lock_team(self):
        Team.objects.select_for_update().get(pk=self.membership.team_id)
        self.membership.refresh_from_db()

    @transaction.atomic
    def perform_create(self, serializer):
        self.lock_team()
        if self.membership.role == "member" and serializer.validated_data.get("assignee") is not None:
            raise PermissionDenied("Only owners or managers can assign requests.")
        obj = serializer.save(team=self.membership.team, created_by=self.membership)
        audit(obj.team, self.request.user, "request.created", obj, snapshot(obj))

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self.lock_team()
        obj = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs["pk"])
        if self.membership.role == "member":
            if obj.created_by_id != self.membership.pk and obj.assignee_id != self.membership.pk:
                raise PermissionDenied("You can update requests you submitted or are assigned.")
            if "assignee" in request.data:
                raise PermissionDenied("Only owners or managers can assign requests.")
            if obj.created_by_id != self.membership.pk and set(request.data) - {"status"}:
                raise PermissionDenied("Assignees can only change status.")
        before = snapshot(obj)
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        after = snapshot(obj)
        audit(
            obj.team,
            request.user,
            "request.updated",
            obj,
            {key: {"from": before[key], "to": value} for key, value in after.items() if before[key] != value},
        )
        return Response(serializer.data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self.lock_team()
        self.require_manager()
        obj = self.get_object()
        audit(obj.team, request.user, "request.deleted", obj, snapshot(obj))
        obj.delete()
        return Response(status=204)


class ImportViewSet(TenantMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ImportSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return ImportJob.objects.filter(team_id=self.kwargs.get("team_id"))

    @extend_schema(request=ImportUploadSerializer, responses={202: ImportSerializer, 200: ImportSerializer})
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        Team.objects.select_for_update().get(pk=self.membership.team_id)
        self.membership.refresh_from_db()
        self.require_manager()
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        if upload.size > 1024 * 1024:
            raise ValidationError("CSV must be at most 1 MiB.")
        raw = upload.read(1024 * 1024 + 1)
        try:
            payload = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError("CSV must use UTF-8 encoding.")
        digest = hashlib.sha256(raw).hexdigest()
        job, created = ImportJob.objects.get_or_create(
            team=self.membership.team,
            idempotency_key=serializer.validated_data["idempotency_key"],
            defaults={"submitted_by": self.membership, "payload": payload, "content_hash": digest},
        )
        if not created and job.content_hash != digest:
            return Response({"detail": "Idempotency key already used for different content."}, status=409)
        if created:
            audit(job.team, request.user, "import.submitted", job, {"content_hash": digest})
            # The durable job is the outbox. Beat will recover if Redis is unavailable.
            from .tasks import enqueue

            transaction.on_commit(lambda: enqueue(str(job.pk)))
        return Response(ImportSerializer(job).data, status=202 if created else 200)


class AuditViewSet(TenantMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditSerializer

    def get_queryset(self):
        return AuditLog.objects.filter(team_id=self.kwargs.get("team_id"))


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})

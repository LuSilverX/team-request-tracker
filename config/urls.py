from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenBlacklistView

from tracker.views import (
    AuditViewSet,
    ImportViewSet,
    LoginView,
    MembershipViewSet,
    RefreshView,
    RegisterView,
    RequestViewSet,
    TeamViewSet,
    health,
)

router = SimpleRouter()
router.register("teams", TeamViewSet, basename="team")
tenant = SimpleRouter()
tenant.register("members", MembershipViewSet, basename="member")
tenant.register("requests", RequestViewSet, basename="request")
tenant.register("imports", ImportViewSet, basename="import")
tenant.register("audit", AuditViewSet, basename="audit")
urlpatterns = [
    path("health/", health),
    path("api/auth/register/", RegisterView.as_view()),
    path("api/auth/token/", LoginView.as_view()),
    path("api/auth/refresh/", RefreshView.as_view()),
    path("api/auth/logout/", TokenBlacklistView.as_view()),
    path("api/", include(router.urls)),
    path("api/teams/<uuid:team_id>/", include(tenant.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
]

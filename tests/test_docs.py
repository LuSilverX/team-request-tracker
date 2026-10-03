import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_swagger_page_renders_for_anonymous_browser():
    response = APIClient().get("/api/docs/", HTTP_ACCEPT="text/html")
    assert response.status_code == 200
    assert "text/html" in response["Content-Type"]
    assert b"SwaggerUIBundle" in response.content
    assert b"/api/schema/" in response.content

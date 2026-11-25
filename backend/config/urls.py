from django.urls import URLPattern, URLResolver, include, path

from core.health import healthz, readyz

api_v1: list[URLPattern | URLResolver] = []  # each app adds its routes here

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("api/v1/", include((api_v1, "api"), namespace="v1")),
]

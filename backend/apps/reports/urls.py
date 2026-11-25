"""Reports routes under /api/v1/."""

from django.urls import path

from apps.reports.views import ChamaHomeView

urlpatterns = [
    path("chama/home", ChamaHomeView.as_view(), name="chama-home"),
]

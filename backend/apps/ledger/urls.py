"""Ledger routes under /api/v1/."""

from django.urls import path

from apps.ledger.views import ReceiptsView

urlpatterns = [
    path("chama/receipts", ReceiptsView.as_view(), name="chama-receipts"),
]

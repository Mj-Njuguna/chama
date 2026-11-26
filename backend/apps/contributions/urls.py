"""Contributions routes under /api/v1/."""

from django.urls import path

from apps.contributions.views import (
    CashbookView,
    CollectionView,
    CurrentCycleView,
    FineWaiveView,
    ObligationsView,
    StatementView,
    WaiverApproveView,
    WaiversView,
)

urlpatterns = [
    path("chama/cycles/current", CurrentCycleView.as_view(), name="chama-cycle-current"),
    path("chama/obligations", ObligationsView.as_view(), name="chama-obligations"),
    path("chama/collection", CollectionView.as_view(), name="chama-collection"),
    path("chama/statement", StatementView.as_view(), name="chama-statement"),
    path("chama/cashbook", CashbookView.as_view(), name="chama-cashbook"),
    path(
        "chama/fines/<uuid:obligation_id>/waive",
        FineWaiveView.as_view(),
        name="chama-fine-waive",
    ),
    path("chama/waivers", WaiversView.as_view(), name="chama-waivers"),
    path(
        "chama/waivers/<uuid:waiver_id>/approve",
        WaiverApproveView.as_view(),
        name="chama-waiver-approve",
    ),
]

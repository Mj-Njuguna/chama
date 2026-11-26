"""Payments routes under /api/v1/.

Provider callback paths avoid the words Safaricom refuses in production callback URLs
("mpesa", "M-PESA", "safaricom", "exe", "sql", "query").
"""

from django.urls import path

from apps.payments import views

urlpatterns = [
    path(
        "payments/c2b/<str:token>/validation",
        views.c2b_validation,
        name="payments-c2b-validation",
    ),
    path(
        "payments/c2b/<str:token>/confirmation",
        views.c2b_confirmation,
        name="payments-c2b-confirmation",
    ),
    path("payments/stk-result/<str:token>", views.stk_result, name="payments-stk-result"),
    path("chama/payment-linking", views.PaymentLinkingView.as_view(), name="chama-payment-linking"),
    path(
        "chama/payment-linking/apply",
        views.PaymentLinkingApplyView.as_view(),
        name="chama-payment-linking-apply",
    ),
    path("chama/payments", views.PaymentsView.as_view(), name="chama-payments"),
    path("chama/payments/stk", views.StkRequestsView.as_view(), name="chama-payments-stk"),
    path(
        "chama/payments/stk/<uuid:stk_request_id>",
        views.StkRequestView.as_view(),
        name="chama-payments-stk-status",
    ),
    path(
        "chama/payments/unallocated",
        views.UnallocatedPaymentsView.as_view(),
        name="chama-payments-unallocated",
    ),
    path(
        "chama/payments/<uuid:payment_id>/allocate",
        views.AllocatePaymentView.as_view(),
        name="chama-payment-allocate",
    ),
]

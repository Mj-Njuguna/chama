from django.urls import path

from tests.rls_probe import views

urlpatterns = [
    path("probe/records", views.records),
    path("probe/naive", views.naive_create),
    path("probe/fail", views.create_then_fail),
    path("probe/memberships", views.my_memberships),
    path("probe/chama-totals", views.ChamaTotalsProbe.as_view()),
    path("probe/list", views.RecordList.as_view()),
    path("probe/list-other", views.OtherList.as_view()),
]

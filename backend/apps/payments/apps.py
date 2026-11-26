from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "apps.payments"
    label = "payments"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from apps.payments import checks  # noqa: F401  registers the system checks

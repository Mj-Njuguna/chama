from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "apps.notifications"
    label = "notifications"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from apps.notifications import checks  # noqa: F401  registers the system checks
        from apps.notifications.handlers import on_payment_allocated, on_payment_confirmed
        from apps.payments.events import PaymentAllocated, PaymentConfirmed
        from core.events import subscribe

        subscribe(PaymentConfirmed, on_payment_confirmed)
        subscribe(PaymentAllocated, on_payment_allocated)

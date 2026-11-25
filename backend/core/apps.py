from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared, model-free package. Installed only so its system checks are registered."""

    name = "core"
    label = "core"

    def ready(self) -> None:
        from core import checks  # noqa: F401  (registers the checks)

from django.apps import AppConfig
from django.db.models.signals import post_migrate

# contrib.auth fills auth_permission after every migrate. Permissions are never granted through
# contrib.auth, so its handler is disconnected and the auth tables stay empty.
CREATE_PERMISSIONS_UID = "django.contrib.auth.management.create_permissions"


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Runs after AuthConfig.ready(): contrib.auth is listed before the domain apps.
        post_migrate.disconnect(dispatch_uid=CREATE_PERMISSIONS_UID)
        from apps.accounts import schema  # noqa: F401  registers the OpenAPI auth extension

"""identity.User: phone-identified, passwordless, outside chama RLS."""

import uuid

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction

from apps.accounts.models import User
from apps.organizations.models import Chama
from core.phones import InvalidPhone

pytestmark = pytest.mark.django_db


def test_user_model_is_identity_user() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model() is User


def test_create_user_normalizes_phone_and_has_no_usable_password() -> None:
    user = User.objects.create_user("0712 345 678")
    user.refresh_from_db()
    assert user.phone == "+254712345678"
    assert isinstance(user.id, uuid.UUID) and user.id.version == 4
    assert not user.has_usable_password()
    assert user.check_password("") is False


def test_create_user_rejects_invalid_phone() -> None:
    with pytest.raises(InvalidPhone):
        User.objects.create_user("0201234567")  # fixed line
    assert not User.objects.exists()


def test_phone_is_globally_unique() -> None:
    User.objects.create_user("0712345678")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("+254 712 345 678")


def test_database_refuses_non_e164_phone() -> None:
    # The model layer is bypassed on purpose: the CHECK constraint is the last line of defense.
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_user (id, phone, password, display_name, is_active, created_at)"
            " VALUES (%s, %s, '!', '', true, now())",
            [uuid.uuid4(), "0712345678"],
        )


def test_passwords_cannot_be_set() -> None:
    user = User.objects.create_user("0712345678")
    with pytest.raises(ValueError, match="no passwords"):
        user.set_password("hunter2")


def test_no_password_authentication_backend(settings) -> None:
    User.objects.create_user("0712345678")
    # No backend at all: Django refuses outright rather than trying a password check.
    assert settings.AUTHENTICATION_BACKENDS == []
    with pytest.raises(ImproperlyConfigured, match="No authentication backends"):
        authenticate(phone="+254712345678", password="")


def test_superusers_cannot_be_created() -> None:
    with pytest.raises(NotImplementedError):
        User.objects.create_superuser("0712345678")


def test_users_carry_no_django_permissions() -> None:
    user = User.objects.create_user("0712345678")
    assert not hasattr(user, "has_perm")
    assert not hasattr(user, "groups")
    assert not hasattr(user, "is_superuser")


def test_str_never_shows_the_phone_number() -> None:
    user = User.objects.create_user("0712345678")
    assert "712345678" not in str(user)


def test_no_admin_and_no_session_table() -> None:
    assert "django.contrib.admin" not in settings.INSTALLED_APPS
    assert "django.contrib.sessions" not in settings.INSTALLED_APPS


def test_chama_has_only_name_added() -> None:
    fields = {f.name for f in Chama._meta.get_fields() if f.concrete}
    # The freeze ("this wasn't me"). Sensitive data never lives here.
    assert fields == {"id", "name", "status", "created_at", "frozen_at", "frozen_by_user_id"}
    assert Chama.objects.create().name == ""


def test_every_creation_path_leaves_the_password_unusable() -> None:
    User.objects.create_user("0712000001")
    User.objects.create(phone="+254712000002")  # password left empty
    User(phone="+254712000003").save()
    sneaky = User(phone="+254712000004", password="pbkdf2_sha256$1000000$salt$hash")
    sneaky.save()
    users = list(User.objects.all())
    assert len(users) == 4
    assert all(not u.has_usable_password() for u in users)
    assert all(u.password.startswith("!") for u in users)


@pytest.mark.parametrize("password", ["", "pbkdf2_sha256$1000000$salt$hash"])
def test_database_refuses_a_usable_password(password: str) -> None:
    # bulk_create skips save(); the CHECK constraint is what stops it.
    refused = pytest.raises(IntegrityError, match="accounts_user_password_unusable")
    with refused, transaction.atomic():
        User.objects.bulk_create([User(phone="+254712000005", password=password)])
    refused = pytest.raises(IntegrityError, match="accounts_user_password_unusable")
    with refused, transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_user (id, phone, password, display_name, is_active, created_at)"
            " VALUES (%s, '+254712000006', %s, '', true, now())",
            [uuid.uuid4(), password],
        )

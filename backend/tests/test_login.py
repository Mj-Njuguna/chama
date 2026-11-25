"""Login and logout over HTTP: POST /auth/otp/verify, /auth/logout and
/auth/logout-all (AUTH-09), and 401 answers with stable codes."""

import uuid
from collections.abc import Callable

import pytest
from django.test import Client

from apps.accounts import sessions
from apps.accounts.models import AuthSession, Device, User
from apps.accounts.sessions import DeviceInfo, TokenPair
from core.api_errors import ApiError
from core.tenancy import user_context

DEVICE = {"installation_id": "install-0001", "platform": "android", "name": "Pixel"}
PHONE = "+254712345678"


def _error(call: Callable[[], object]) -> ApiError:
    with pytest.raises(ApiError) as info:
        call()
    return info.value


def _login(user_id: uuid.UUID) -> TokenPair:
    return sessions.login(user_id, DeviceInfo("install-0001", "android"), None)


@pytest.mark.django_db
def test_login_over_http_returns_tokens_and_binds_the_device(client: Client, fake_senders):
    sent = client.post(
        "/api/v1/auth/otp/request", {"phone": PHONE}, content_type="application/json"
    )
    challenge = sent.json()["challenge_id"]
    code = fake_senders.outbox(PHONE)[0]["code"]
    response = client.post(
        "/api/v1/auth/otp/verify",
        {"challenge_id": challenge, "code": code, "device": DEVICE},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"].startswith("chm_at_") and body["refresh_token"].startswith(
        "chm_rt_"
    )
    assert body["access_expires_in"] == 900 and body["refresh_expires_in"] == 30 * 86400
    assert body["user"]["is_new"] is True and body["active_chama_id"] is None
    assert body["memberships"] == []
    user_id = uuid.UUID(body["user"]["id"])
    with user_context(user_id):
        assert Device.objects.get().installation_id == "install-0001"


@pytest.mark.django_db
def test_wrong_code_over_http_is_invalid_code(client: Client, fake_senders):
    sent = client.post(
        "/api/v1/auth/otp/request", {"phone": PHONE}, content_type="application/json"
    )
    response = client.post(
        "/api/v1/auth/otp/verify",
        {"challenge_id": sent.json()["challenge_id"], "code": "x", "device": DEVICE},
        content_type="application/json",
    )
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_code"
    assert not AuthSession.objects.exists()


@pytest.mark.django_db
def test_login_selects_the_only_active_chama():
    from apps.organizations.models import Chama, Membership
    from apps.organizations.roles import Role
    from core.tenancy import chama_context

    user = User.objects.create_user(PHONE)
    chama = Chama.objects.create()
    with chama_context(chama.id):
        Membership.objects.create(chama=chama, user=user, role=Role.CHAIR)
    client = Client()
    from apps.accounts import otp

    sent = otp.request_otp(PHONE)
    from apps.notifications import fake

    code = fake.outbox(PHONE)[0]["code"]
    response = client.post(
        "/api/v1/auth/otp/verify",
        {"challenge_id": str(sent.challenge_id), "code": code, "device": DEVICE},
        content_type="application/json",
    )
    body = response.json()
    assert body["active_chama_id"] == str(chama.id)
    assert body["memberships"] == [{"chama_id": str(chama.id), "role": "chair", "status": "active"}]


@pytest.mark.django_db
def test_logout_ends_this_session_only(client: Client):
    user = User.objects.create_user(PHONE)
    mine, other = _login(user.id), sessions.login(user.id, DeviceInfo("install-0002", "ios"), None)
    response = client.post("/api/v1/auth/logout", HTTP_AUTHORIZATION=f"Bearer {mine.access_token}")
    assert response.status_code == 204
    assert _error(lambda: sessions.refresh(mine.refresh_token)).error_code == "session_revoked"
    assert sessions.authenticate_access(other.access_token).user_id == user.id


@pytest.mark.django_db
def test_auth_09_logout_everywhere_ends_every_session(client: Client):
    user = User.objects.create_user(PHONE)
    pairs = [
        sessions.login(user.id, DeviceInfo(f"install-000{i}", "android"), None) for i in range(3)
    ]
    response = client.post(
        "/api/v1/auth/logout-all", HTTP_AUTHORIZATION=f"Bearer {pairs[0].access_token}"
    )
    assert response.status_code == 204
    for pair in pairs:
        token = pair.refresh_token
        assert _error(lambda: sessions.refresh(token)).error_code == "session_revoked"  # noqa: B023
    after = client.post("/api/v1/auth/logout", HTTP_AUTHORIZATION=f"Bearer {pairs[1].access_token}")
    assert after.status_code == 401 and after.json()["error"]["code"] == "session_revoked"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("header", "code"),
    [
        (None, "not_authenticated"),
        ("Bearer nope", "invalid_token"),
        ("Basic abc", "not_authenticated"),
    ],
)
def test_protected_endpoints_answer_401_with_a_code(client: Client, header, code):
    extra = {"HTTP_AUTHORIZATION": header} if header else {}
    response = client.post("/api/v1/auth/logout", **extra)
    assert response.status_code == 401 and response.json()["error"]["code"] == code
    assert response["WWW-Authenticate"].startswith("Bearer")

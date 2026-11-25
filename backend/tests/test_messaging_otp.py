"""OTP sending (apps.notifications): WhatsApp first, SMS once on failure (AUTH-11), a hard timeout,
and the same failure for every number."""

import time

import pytest
from django.core.checks import run_checks
from django.test import Client, override_settings

from apps.notifications.services import OtpChannel, OtpSendFailed, send_otp

PHONE = "+254712345678"
OTHER = "+254722000111"
CODE = "123456"


def test_whatsapp_first(fake_senders):
    result = send_otp(PHONE, CODE)
    assert result.channel == OtpChannel.WHATSAPP and result.failures == ()
    assert [(m["channel"], m["code"]) for m in fake_senders.outbox(PHONE)] == [("whatsapp", CODE)]


@pytest.mark.parametrize("behaviour", ["undeliverable", "failed", "raise"])
def test_auth_11_sms_fallback_used_once_and_failure_recorded(fake_senders, behaviour):
    fake_senders.BEHAVIOUR[(OtpChannel.WHATSAPP, PHONE)] = behaviour
    result = send_otp(PHONE, CODE)
    assert result.channel == OtpChannel.SMS
    reason = "error" if behaviour == "raise" else behaviour
    assert [(f.channel, f.reason) for f in result.failures] == [(OtpChannel.WHATSAPP, reason)]
    assert [m["channel"] for m in fake_senders.outbox(PHONE)] == ["sms"]  # exactly one SMS


@override_settings(MESSAGING_PROVIDER_TIMEOUT_SECONDS=0.2)
def test_provider_calls_have_a_hard_timeout(fake_senders):
    fake_senders.BEHAVIOUR[(OtpChannel.WHATSAPP, PHONE)] = "slow"
    started = time.monotonic()
    result = send_otp(PHONE, CODE)
    assert time.monotonic() - started < 1.0  # did not wait for the slow provider
    assert result.channel == OtpChannel.SMS
    assert result.failures[0].reason == "timeout"


@pytest.mark.parametrize("behaviour", ["undeliverable", "failed", "raise", "slow"])
@override_settings(MESSAGING_PROVIDER_TIMEOUT_SECONDS=0.2)
def test_every_failure_is_the_same_error_whatever_the_number(fake_senders, behaviour):
    for phone in (PHONE, OTHER):
        for channel in OtpChannel:
            fake_senders.BEHAVIOUR[(channel, phone)] = behaviour
    messages = set()
    for phone in (PHONE, OTHER):
        with pytest.raises(OtpSendFailed) as info:
            send_otp(phone, CODE)
        messages.add(str(info.value))
        assert phone[-9:] not in str(info.value) and CODE not in str(info.value)
        assert [f.channel for f in info.value.failures] == [OtpChannel.WHATSAPP, OtpChannel.SMS]
    assert messages == {"the code could not be sent"}
    assert fake_senders.outbox(PHONE) == [] and fake_senders.outbox(OTHER) == []


@override_settings(OTP_SENDERS={})
def test_no_configured_sender_fails_closed(fake_senders):
    with pytest.raises(OtpSendFailed) as info:
        send_otp(PHONE, CODE)
    assert {f.reason for f in info.value.failures} == {"not_configured"}


def test_codes_are_never_logged(fake_senders, caplog):
    fake_senders.BEHAVIOUR[(OtpChannel.WHATSAPP, PHONE)] = "failed"
    send_otp(PHONE, CODE)
    assert CODE not in caplog.text and "712345678" not in caplog.text


# ---------------------------------------------------------------- fakes are dev-only


@override_settings(MESSAGING_ALLOW_FAKE=False, FAKE_OTP_OUTBOX_ENABLED=False)
def test_fake_senders_are_refused_where_fakes_are_not_allowed():
    ids = {m.id for m in run_checks()}
    assert "notifications.E001" in ids and "notifications.E002" not in ids


@override_settings(MESSAGING_ALLOW_FAKE=False, OTP_SENDERS={}, FAKE_OTP_OUTBOX_ENABLED=True)
def test_fake_outbox_is_refused_where_fakes_are_not_allowed():
    ids = {m.id for m in run_checks()}
    assert "notifications.E002" in ids and "notifications.E001" not in ids


def test_dev_outbox_shows_codes_for_a_number(fake_senders):
    send_otp(PHONE, CODE)
    response = Client().get("/dev/otp-outbox", {"phone": "0712 345 678"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == PHONE and body["messages"][0]["code"] == CODE


def test_dev_outbox_rejects_bad_numbers(fake_senders):
    response = Client().get("/dev/otp-outbox", {"phone": "nope"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_phone"


@override_settings(FAKE_OTP_OUTBOX_ENABLED=False)
def test_dev_outbox_view_refuses_even_if_routed(fake_senders):
    assert Client().get("/dev/otp-outbox", {"phone": PHONE}).status_code == 404


@override_settings(
    MESSAGING_ALLOW_FAKE=False,
    OTP_SENDERS={},
    FAKE_OTP_OUTBOX_ENABLED=False,
    PUSH_PROVIDER="",
    INVITE_SENDER="apps.notifications.invites.FakeInviteSender",
)
def test_fake_invite_sender_is_refused_where_fakes_are_not_allowed():
    ids = {m.id for m in run_checks()}
    assert "notifications.E004" in ids and "notifications.E001" not in ids

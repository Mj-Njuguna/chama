"""SecurityEvent: append-only, the three audit policies, safe metadata, and reading a chama's
events."""

import uuid

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from apps.accounts.audit import MAX_METADATA_INT, METADATA_SCHEMA, clean_metadata, subject_hash
from apps.accounts.models import SecurityEvent
from apps.accounts.services import (
    InvalidAuditMetadata,
    SecurityEventKind,
    chama_security_events,
    record_security_event,
    subject_hashes,
)
from apps.organizations.models import Chama
from core.tenancy import chama_context, user_context

pytestmark = pytest.mark.django_db

KIND = SecurityEventKind.LOGIN
PHONE = "+254712345678"


@pytest.fixture
def world():
    """Chama A and B; user U with events in A, B and without a chama; user V likewise."""
    a, b = Chama.objects.create(), Chama.objects.create()
    u, v = uuid.uuid4(), uuid.uuid4()
    with chama_context(a.id):
        record_security_event(KIND, chama_id=a.id, user_id=u, metadata={"reason": "in_a"})
    with chama_context(b.id):
        record_security_event(KIND, chama_id=b.id, user_id=u, metadata={"reason": "in_b"})
    record_security_event(KIND, user_id=u, metadata={"reason": "u_no_chama"})
    record_security_event(KIND, user_id=v, metadata={"reason": "v_no_chama"})
    record_security_event(SecurityEventKind.OTP_VERIFY_FAILED, phone_e164=PHONE)  # no user
    return {"a": a, "b": b, "u": u, "v": v}


def _reasons() -> list[str]:
    return sorted(SecurityEvent.objects.values_list("metadata__reason", flat=True))


# ---------------------------------------------------------------- reads (the policies)


def test_without_context_nothing_is_visible(world):
    assert SecurityEvent.objects.count() == 0


def test_chama_context_sees_only_that_chamas_events(world):
    with chama_context(world["a"].id):
        assert _reasons() == ["in_a"]


def test_user_context_sees_only_own_events_without_chama(world):
    with user_context(world["u"]):
        assert _reasons() == ["u_no_chama"]
    with user_context(world["v"]):
        assert _reasons() == ["v_no_chama"]


def test_both_settings_show_only_the_chamas_events(world):
    with chama_context(world["a"].id), user_context(world["u"]):
        assert _reasons() == ["in_a"]  # not u's events without a chama, not chama B's


# ---------------------------------------------------------------- writes (append-only)


def test_events_without_chama_can_be_written_before_login():
    record_security_event(SecurityEventKind.OTP_SEND_FAILED, phone_e164=PHONE)  # no context


def test_cannot_write_an_event_for_another_chama(world):
    refused = pytest.raises(ProgrammingError, match="row-level security")
    with refused, chama_context(world["a"].id):
        record_security_event(KIND, chama_id=world["b"].id)


def test_cannot_write_an_chama_event_without_its_context(world):
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        record_security_event(KIND, chama_id=world["a"].id)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE accounts_securityevent SET kind = 'logout'",
        "DELETE FROM accounts_securityevent",
        "TRUNCATE accounts_securityevent",
    ],
)
def test_app_cannot_change_or_delete_events(world, sql: str):
    with chama_context(world["a"].id):
        refused = pytest.raises(ProgrammingError, match="permission denied")
        with refused, transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)
        assert _reasons() == ["in_a"]


def test_orm_update_and_delete_are_refused(world):
    with chama_context(world["a"].id):
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            SecurityEvent.objects.update(kind=SecurityEventKind.LOGOUT)
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            SecurityEvent.objects.all().delete()


def test_database_refuses_unknown_kinds():
    refused = pytest.raises(IntegrityError, match="accounts_securityevent_kind_valid")
    with refused, transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_securityevent (id, occurred_at, kind, subject_hash, "
            "ip_hash, metadata) VALUES (%s, now(), 'made_up', '', '', '{}')",
            [uuid.uuid4()],
        )


# ---------------------------------------------------------------- nothing sensitive stored

PHONE_FORMS = [PHONE, "254712345678", "0712345678", "712345678", "0712 345 678", "+254-712"]
OTP_FORMS = ["123456", "012345", 123456, 999999]
TOKEN_FORMS = [
    "chm_" + "A1b2C3d4" * 5,
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopq",  # 43 letters, like a token without digits
]


@pytest.mark.parametrize("key", sorted(METADATA_SCHEMA))
@pytest.mark.parametrize("value", [*PHONE_FORMS, *OTP_FORMS, *TOKEN_FORMS])
def test_metadata_can_never_hold_a_phone_otp_or_token(key: str, value: object):
    with pytest.raises(InvalidAuditMetadata) as info:
        record_security_event(KIND, metadata={key: value})
    assert str(value) not in str(info.value)  # the error never echoes the value


@pytest.mark.parametrize("key", ["phone", "otp", "code", "token", "ip", "msisdn", "note"])
def test_metadata_keys_outside_the_schema_are_refused(key: str):
    with pytest.raises(InvalidAuditMetadata):
        record_security_event(KIND, metadata={key: "x"})


@pytest.mark.parametrize("value", [-1, MAX_METADATA_INT + 1, True, 1.5, None, [], {}])
def test_metadata_integers_are_small_and_really_integers(value: object):
    with pytest.raises(InvalidAuditMetadata):
        record_security_event(KIND, metadata={"attempts": value})


@pytest.mark.parametrize("value", ["k1", "k2026", "main"])
def test_key_ids_fit_their_own_narrow_shape(value: str):
    assert clean_metadata({"key_id": value, "operator": "ops_oncall"})["key_id"] == value


@pytest.mark.parametrize("value", ["k12345", "1", "K1", "k_1", "abcdefghi1"])
def test_key_id_shape_refuses_long_digit_runs_and_other_forms(value: str):
    with pytest.raises(InvalidAuditMetadata):
        clean_metadata({"key_id": value})


def test_stored_row_holds_no_phone_or_ip_in_any_column():
    user = uuid.uuid4()
    record_security_event(
        SecurityEventKind.OTP_VERIFY_FAILED,
        user_id=user,
        phone_e164=PHONE,
        ip="203.0.113.7",
        metadata={"attempts": 3, "channel": "sms"},
    )
    with user_context(user):
        row = SecurityEvent.objects.values().get()
    text = repr(row)
    for needle in ("712345678", "203.0.113"):
        assert needle not in text
    assert row["subject_hash"] == subject_hash(PHONE)
    assert row["subject_hash"].startswith("dev1:") and row["ip_hash"].startswith("dev1:")
    assert row["metadata"] == {"attempts": 3, "channel": "sms"}


def test_subject_hash_is_keyed_and_lookups_cover_every_key(settings):
    first = subject_hash(PHONE)
    settings.AUDIT_HASH_KEYS = "k2:" + "B" * 43 + "," + settings.AUDIT_HASH_KEYS
    assert subject_hash(PHONE).startswith("k2:")
    assert subject_hashes(PHONE) == [subject_hash(PHONE), first]  # old events still found


# ---------------------------------------------------------------- reading chama events
# Who may read them is the role matrix (view_security_events, officials only) behind
# RequiresAction on GET /chama/security-events; here: the reader sees only its own chama.


def test_chama_events_reader_sees_only_the_context_chama(world):
    with chama_context(world["a"].id):
        events = chama_security_events()
        assert [e.metadata["reason"] for e in events] == ["in_a"]
        assert not hasattr(events[0], "subject_hash")
    assert chama_security_events() == []  # no context at all

"""SEC-02: field encryption at rest with AES-256-GCM and a key ring."""

import io
import uuid

import pytest
from django.core.exceptions import FieldError
from django.core.management import call_command
from django.db import connection

from apps.organizations.models import Chama
from core import crypto
from core.crypto import DecryptionFailed
from core.tenancy import chama_context
from tests.rls_probe.models import ProbeSecret

pytestmark = pytest.mark.django_db

SECRET = "paybill 600123 chama SAVINGS"
LABEL = "rls_probe_probesecret.secret"
NEW_KEY = "k2:" + "N" * 43
# Made once with the dev ring (key "dev1"); pins the stored format and the HKDF derivation.
KNOWN_ANSWER = (
    "fe1:dev1:Is6yPmJzRhAp-FfRuhlf3cR19dukkZ_l96LLQ_fpopr9VtuYq3LS8NncCLKJsr13kkxqQ8HNkOQ"
)


@pytest.fixture
def chama() -> Chama:
    return Chama.objects.create()


def _stored(chama: Chama, pk: uuid.UUID) -> str:
    with chama_context(chama.id), connection.cursor() as cursor:
        cursor.execute("SELECT secret FROM rls_probe_probesecret WHERE id = %s", [pk])
        return str(cursor.fetchone()[0])


def _save(chama: Chama, value: str | None) -> ProbeSecret:
    with chama_context(chama.id):
        return ProbeSecret.objects.create(chama=chama, secret=value)


def _read(chama: Chama, pk: uuid.UUID) -> str | None:
    with chama_context(chama.id):
        value: str | None = ProbeSecret.objects.get(pk=pk).secret
        return value


def test_sec_02_round_trip_and_only_ciphertext_at_rest(chama):
    row = _save(chama, SECRET)
    assert _read(chama, row.pk) == SECRET
    stored = _stored(chama, row.pk)
    assert stored.startswith("fe1:dev1:")
    assert SECRET not in stored and "600123" not in stored
    again = _save(chama, SECRET)
    assert _stored(chama, again.pk) != stored  # a fresh nonce every time


def test_null_stays_null(chama):
    row = _save(chama, None)
    assert _read(chama, row.pk) is None
    with chama_context(chama.id):
        assert ProbeSecret.objects.filter(secret__isnull=True).count() == 1


def test_a_changed_byte_never_decrypts(chama):
    row = _save(chama, SECRET)
    stored = _stored(chama, row.pk)
    flipped = stored[:-2] + ("A" if stored[-2] != "A" else "B") + stored[-1]
    with chama_context(chama.id), connection.cursor() as cursor:
        sql = "UPDATE rls_probe_probesecret SET secret = %s WHERE id = %s"
        cursor.execute(sql, [flipped, row.pk])
    with pytest.raises(DecryptionFailed):
        _read(chama, row.pk)


@pytest.mark.parametrize(
    "stored",
    ["plain text", "fe1:dev1", "fe1::abc", "fe2:dev1:abc", "fe1:dev1:!!!", "fe1:dev1:YWJj"],
)
def test_malformed_values_are_refused(stored):
    with pytest.raises(DecryptionFailed):
        crypto.decrypt(stored, LABEL)


def test_a_value_is_bound_to_its_column():
    stored = crypto.encrypt(SECRET, LABEL)
    assert crypto.decrypt(stored, LABEL) == SECRET
    with pytest.raises(DecryptionFailed):
        crypto.decrypt(stored, "rls_probe_probesecret.other")


def test_known_answer_vector():
    assert crypto.decrypt(KNOWN_ANSWER, LABEL) == SECRET


def test_an_unknown_key_id_fails(settings):
    stored = crypto.encrypt(SECRET, LABEL)
    settings.FIELD_ENCRYPTION_KEYS = NEW_KEY
    with pytest.raises(DecryptionFailed, match="unknown key"):
        crypto.decrypt(stored, LABEL)


def test_no_equality_or_search_on_ciphertext():
    with pytest.raises(FieldError):
        list(ProbeSecret.objects.filter(secret=SECRET))
    with pytest.raises(FieldError):
        list(ProbeSecret.objects.filter(secret__icontains="paybill"))


def test_rotation_reencrypts_then_the_old_key_can_go(settings, chama):
    old_ring = settings.FIELD_ENCRYPTION_KEYS
    rows = [_save(chama, SECRET), _save(chama, "second"), _save(chama, None)]
    other = Chama.objects.create()
    _save(other, "other chama")

    settings.FIELD_ENCRYPTION_KEYS = f"{NEW_KEY},{old_ring}"  # prepend, deploy
    out = io.StringIO()
    call_command("reencrypt_fields", "--check", stdout=out)
    assert "rls_probe.ProbeSecret: 3 rows on an older key" in out.getvalue()
    assert _stored(chama, rows[0].pk).startswith("fe1:dev1:")  # --check changes nothing

    call_command("reencrypt_fields", stdout=io.StringIO())
    assert _stored(chama, rows[0].pk).startswith("fe1:k2:")
    out = io.StringIO()
    call_command("reencrypt_fields", "--check", stdout=out)
    assert "total: 0" in out.getvalue()
    assert SECRET not in out.getvalue()

    settings.FIELD_ENCRYPTION_KEYS = NEW_KEY  # remove the old key
    assert [_read(chama, r.pk) for r in rows] == [SECRET, "second", None]
    with chama_context(other.id):
        assert ProbeSecret.objects.get().secret == "other chama"


def test_ring_keys_are_never_used_directly():
    ring = crypto.key_ring()
    key = ring.key(ring.current_id)
    assert key is not None
    derived = crypto._aead(key)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = b"\x00" * crypto.NONCE_BYTES
    raw_key = AESGCM(key[:32]).encrypt(nonce, b"x", None)
    assert derived.encrypt(nonce, b"x", None) != raw_key  # HKDF output, not the ring key

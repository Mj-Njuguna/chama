"""Key rings with key IDs (core.keys): rotation without a flag day."""

import base64

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.keys import dev_key_ring_value, parse_key_ring


def _key(fill: bytes) -> str:
    return base64.urlsafe_b64encode(fill * 32).decode().rstrip("=")


OLD, NEW = f"k1:{_key(b'a')}", f"k2:{_key(b'b')}"


def test_sign_uses_the_current_key_and_names_it():
    ring = parse_key_ring("X", f"{NEW},{OLD}")
    signed = ring.sign("p", b"data")
    assert signed.startswith("k2:") and len(signed) == 3 + 64
    assert ring.current_id == "k2" and ring.key_ids == ("k2", "k1")


def test_values_signed_before_a_rotation_still_verify():
    before = parse_key_ring("X", OLD).sign("p", b"data")
    after = parse_key_ring("X", f"{NEW},{OLD}")
    assert after.verify("p", b"data", before)
    assert after.sign("p", b"data") != before  # new values use the new key
    assert before in after.sign_all("p", b"data")


def test_removed_keys_and_tampering_fail():
    before = parse_key_ring("X", OLD).sign("p", b"data")
    ring = parse_key_ring("X", NEW)
    assert not ring.verify("p", b"data", before)  # k1 retired
    signed = ring.sign("p", b"data")
    assert not ring.verify("p", b"other", signed)
    assert not ring.verify("other-purpose", b"data", signed)
    assert not ring.verify("p", b"data", "k2:")
    assert not ring.verify("p", b"data", "garbage")


def test_purposes_never_collide():
    ring = parse_key_ring("X", NEW)
    assert ring.sign("a", b"data") != ring.sign("b", b"data")


@pytest.mark.parametrize(
    "raw",
    ["", " , ", "k1", f"k1:{_key(b'a')[:20]}", f"K1:{_key(b'a')}", f"{OLD},{OLD}", "k1:@@@"],
)
def test_malformed_rings_are_refused(raw: str):
    with pytest.raises(ImproperlyConfigured, match="X"):
        parse_key_ring("X", raw)


def test_key_material_never_appears_in_repr():
    ring = parse_key_ring("X", NEW)
    assert _key(b"b") not in repr(ring)
    assert "b" * 8 not in repr(ring)


def test_dev_rings_are_valid_distinct_and_fixed():
    audit, other = dev_key_ring_value("audit hash"), dev_key_ring_value("token")
    assert parse_key_ring("X", audit).current_id == "dev1"
    assert audit != other and audit == dev_key_ring_value("audit hash")

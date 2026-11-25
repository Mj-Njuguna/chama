"""Server step-up PIN: hashing, rules, set, change, lockout (AUTH-17), daily cap."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import extract_parameters
from django.utils import timezone

from apps.accounts import pin as pins
from apps.accounts import sessions
from apps.accounts.models import SecurityEvent, StepUpPin, User
from apps.accounts.sessions import DeviceInfo, SessionIdentity
from core.api_errors import ApiError
from core.ratelimit import RateLimitUnavailable
from core.tenancy import user_context

pytestmark = pytest.mark.django_db

PHONE = "+254712345678"
GOOD = "275931"
OTHER_GOOD = "802461"


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 09:00 in Nairobi, so a test never crosses midnight by accident.
        self.now = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)
        monkeypatch.setattr(timezone, "now", lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    return Clock(monkeypatch)


@pytest.fixture(autouse=True)
def session_limit(redis_db, settings):
    """Clean Redis; the per-session limit is lifted except in its own tests (lockout tests make
    more than ten checks on one session within the real-time window)."""
    settings.PIN_VERIFY_LIMIT_PER_SESSION = (1000, 900)


@pytest.fixture
def user() -> User:
    return User.objects.create_user(PHONE)


@pytest.fixture
def events(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    from apps.accounts import audit

    seen: list[tuple[str, dict[str, Any]]] = []

    def spy(kind, **kwargs):
        seen.append((str(kind), kwargs.get("metadata") or {}))
        return audit.record_security_event(kind, **kwargs)

    monkeypatch.setattr("apps.accounts.pin.record_security_event", spy)
    return seen


def _identity(user: User, installation: str = "install-0001") -> SessionIdentity:
    pair = sessions.login(user.id, DeviceInfo(installation, "android"), None)
    return sessions.authenticate_access(pair.access_token)


def _reload(identity: SessionIdentity) -> SessionIdentity:
    """The identity as the next request sees it (step-up times come from the session row)."""
    from apps.accounts.models import AuthSession

    row = AuthSession.objects.get(id=identity.session_id)
    return SessionIdentity(
        identity.session_id,
        identity.user_id,
        identity.device_id,
        identity.active_chama_id,
        row.step_up_pin_at,
        row.step_up_otp_at,
        row.login_verified_at,
    )


def _fresh_otp(identity: SessionIdentity) -> SessionIdentity:
    sessions.mark_step_up(identity, "otp", timezone.now())
    return _reload(identity)


def _with_pin(user: User, value: str = GOOD) -> SessionIdentity:
    identity = _fresh_otp(_identity(user))
    pins.set_first_pin(identity, value)
    return _reload(identity)


def _error(call) -> ApiError:
    with pytest.raises(ApiError) as info:
        call()
    return info.value


def _row(user: User) -> StepUpPin:
    with user_context(user.id):
        return StepUpPin.objects.get(user_id=user.id)


# ---------------------------------------------------------------- hashing


def test_only_a_peppered_argon2id_hash_is_stored(user, settings):
    _with_pin(user)
    row = _row(user)
    assert GOOD not in row.pin_hash
    params = extract_parameters(row.pin_hash)
    assert row.pin_hash.startswith("$argon2id$")
    assert (params.time_cost, params.memory_cost, params.parallelism) == (
        settings.PIN_ARGON2_TIME_COST,
        settings.PIN_ARGON2_MEMORY_KIB,
        settings.PIN_ARGON2_PARALLELISM,
    )
    assert row.pepper_key_id == settings.PIN_PEPPER_KEYS.split(":")[0]
    other = User.objects.create_user("0712000999")
    _with_pin(other)
    assert _row(other).pin_hash != row.pin_hash  # same PIN, different users


def test_production_cost_defaults():
    from config.settings import base

    assert (base.PIN_ARGON2_TIME_COST, base.PIN_ARGON2_MEMORY_KIB) == (3, 64 * 1024)
    assert base.PIN_ARGON2_PARALLELISM == 1


def test_changed_parameters_are_upgraded_on_the_next_success(user, settings):
    identity = _with_pin(user)
    settings.PIN_ARGON2_MEMORY_KIB = 16 * 1024
    pins.verify_pin(identity, GOOD)
    assert extract_parameters(_row(user).pin_hash).memory_cost == 16 * 1024


def test_a_new_pepper_key_is_adopted_on_the_next_success(user, settings):
    identity = _with_pin(user)
    settings.PIN_PEPPER_KEYS = "k9:" + "Q" * 43 + "," + settings.PIN_PEPPER_KEYS
    pins.verify_pin(identity, GOOD)
    assert _row(user).pepper_key_id == "k9"
    pins.verify_pin(identity, GOOD)


def test_a_removed_pepper_key_means_the_pin_must_be_set_again(user, settings):
    identity = _with_pin(user)
    settings.PIN_PEPPER_KEYS = "k9:" + "Q" * 43
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "pin_not_set"
    assert pins.has_pin(user.id) is False  # so actions answer pin_not_set and /me says no PIN


def test_a_pin_whose_pepper_key_is_missing_is_replaced_with_a_fresh_otp(
    user, clock, settings, events
):
    identity = _with_pin(user)
    _fail(identity, 3)
    set_at = _row(user).set_at
    settings.PIN_PEPPER_KEYS = "k9:" + "Q" * 43
    clock.advance(301)  # neither the login nor the earlier OTP is fresh
    stale = _reload(identity)
    assert _error(lambda: pins.set_first_pin(stale, OTHER_GOOD)).error_code == "step_up_required"
    pins.set_first_pin(_fresh_otp(stale), OTHER_GOOD)
    row = _row(user)  # the same row, updated
    assert (row.pepper_key_id, row.failed_attempts, row.day_failures) == ("k9", 0, 3)
    assert row.set_at > set_at
    assert events[-1] == ("pin_set", {"count": 0, "reason": "pepper_key_missing"})
    pins.verify_pin(_reload(identity), OTHER_GOOD)


def test_replacing_a_pin_with_a_missing_key_never_rides_on_the_login(user, clock, settings):
    _with_pin(user)
    settings.PIN_PEPPER_KEYS = "k9:" + "Q" * 43
    just_logged_in = _identity(user, "install-0002")  # login fresh, no step-up OTP
    error = _error(lambda: pins.set_first_pin(just_logged_in, OTHER_GOOD))
    assert (error.error_code, error.extra["factors"]) == ("step_up_required", ["otp"])


def test_a_checkable_pin_is_never_replaced_by_set(user):
    identity = _fresh_otp(_with_pin(user))
    assert _error(lambda: pins.set_first_pin(identity, OTHER_GOOD)).error_code == (
        "pin_already_set"
    )


# ---------------------------------------------------------------- rules


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("12345", "not_six_digits"),
        ("1234567", "not_six_digits"),
        ("12a456", "not_six_digits"),
        ("١٢٣٤٥٦", "not_six_digits"),  # non-ASCII digits
        ("000000", "repeated"),
        ("777777", "repeated"),
        ("121212", "repeated"),
        ("123123", "repeated"),
        ("012345", "sequence"),
        ("456789", "sequence"),
        ("987654", "sequence"),
    ],
)
def test_weak_pins_are_refused(user, value, reason):
    identity = _fresh_otp(_identity(user))
    error = _error(lambda: pins.set_first_pin(identity, value))
    assert (error.error_code, error.extra["reason"]) == ("pin_rejected", reason)


def test_the_last_six_digits_of_the_users_own_phone_are_refused():
    other = User.objects.create_user("0711928374")
    identity = _fresh_otp(_identity(other))
    error = _error(lambda: pins.set_first_pin(identity, "928374"))
    assert (error.error_code, error.extra["reason"]) == ("pin_rejected", "phone_digits")


@pytest.mark.parametrize("value", [GOOD, OTHER_GOOD, "102938", "112233", "135790"])
def test_ordinary_pins_are_accepted(value):
    assert pins.rejection_reason(value, PHONE) is None


# ---------------------------------------------------------------- first set


def test_login_counts_only_for_the_first_pin_and_only_for_five_minutes(user, clock):
    identity = _identity(user)
    assert (identity.login_verified_at, identity.step_up_otp_at) == (clock.now, None)
    clock.advance(300)
    pins.set_first_pin(identity, GOOD)  # onboarding: no second code right after login


def test_a_login_older_than_five_minutes_does_not_set_the_first_pin(user, clock):
    identity = _identity(user)
    clock.advance(301)
    assert _error(lambda: pins.set_first_pin(identity, GOOD)).error_code == "step_up_required"


def test_first_pin_needs_a_fresh_otp(user, clock, events):
    identity = _identity(user)
    clock.advance(301)  # the login's OTP is no longer fresh
    error = _error(lambda: pins.set_first_pin(identity, GOOD))
    assert (error.error_code, error.extra["factors"], error.status_code) == (
        "step_up_required",
        ["otp"],
        403,
    )
    identity = _fresh_otp(identity)
    clock.advance(301)  # older than STEP_UP_OTP_FRESH_SECONDS
    assert _error(lambda: pins.set_first_pin(identity, GOOD)).error_code == "step_up_required"
    identity = _fresh_otp(identity)
    pins.set_first_pin(identity, GOOD)
    assert events == [("pin_set", {"count": 0})]  # no other signed-in device
    assert _reload(identity).step_up_pin_at == clock.now  # setting it counts as a fresh PIN


def test_first_pin_alerts_the_users_other_signed_in_devices(user, events):
    from apps.accounts import devices
    from apps.notifications import fake

    first = _identity(user, "install-0001")
    devices.register_push_token(first, "expo", "ExponentPushToken[first]")
    third = _identity(user, "install-0003")  # signed in, no push token yet
    devices.end_session(_identity(user, "install-0004"))  # signed out: not counted
    current = _identity(user, "install-0002")
    devices.register_push_token(current, "expo", "ExponentPushToken[current]")
    fake.PUSHED.clear()
    pins.set_first_pin(current, GOOD)
    assert events == [("pin_set", {"count": 2})]  # install-0001 and install-0003
    assert [(t, token, m.kind, m.data) for t, token, m in fake.PUSHED] == [
        ("expo", "ExponentPushToken[first]", "pin_set", {})
    ]
    assert third.device_id != current.device_id
    fake.PUSHED.clear()


def test_a_failing_push_never_fails_setting_the_first_pin(user, events, monkeypatch):
    from apps.accounts import devices
    from apps.notifications import fake

    other = _identity(user, "install-0001")
    devices.register_push_token(other, "expo", "ExponentPushToken[first]")

    def broken(self, token_type, token, message):
        raise ConnectionError("provider down")

    monkeypatch.setattr(fake.FakePushProvider, "send", broken)
    pins.set_first_pin(_identity(user, "install-0002"), GOOD)
    assert pins.has_pin(user.id) and events == [("pin_set", {"count": 1})]


def test_first_pin_only_once(user):
    identity = _with_pin(user)
    assert _error(lambda: pins.set_first_pin(identity, OTHER_GOOD)).error_code == (
        "pin_already_set"
    )


def test_no_pin_means_pin_not_set(user):
    identity = _identity(user)
    error = _error(lambda: pins.verify_pin(identity, GOOD))
    assert (error.error_code, error.status_code) == ("pin_not_set", 403)
    assert pins.has_pin(user.id) is False


# ---------------------------------------------------------------- verify and change


def test_correct_pin_makes_the_pin_step_up_fresh(user, clock):
    identity = _with_pin(user)
    clock.advance(60)
    pins.verify_pin(identity, GOOD)
    assert _reload(identity).step_up_pin_at == clock.now


def test_wrong_pins_count_down(user):
    identity = _with_pin(user)
    lefts = [_error(lambda: pins.verify_pin(identity, OTHER_GOOD)).extra for _ in range(4)]
    assert [e["attempts_left"] for e in lefts] == [4, 3, 2, 1]
    pins.verify_pin(identity, GOOD)  # success resets the run
    assert _error(lambda: pins.verify_pin(identity, OTHER_GOOD)).extra["attempts_left"] == 4


def test_change_pin_needs_the_current_pin(user, events):
    identity = _with_pin(user)
    assert _error(lambda: pins.change_pin(identity, OTHER_GOOD, "802462")).error_code == (
        "invalid_pin"
    )
    assert _row(user).failed_attempts == 1  # a wrong current PIN counts
    assert _error(lambda: pins.change_pin(identity, GOOD, "111111")).error_code == "pin_rejected"
    assert _row(user).failed_attempts == 1  # a weak new PIN costs no attempt
    pins.change_pin(identity, GOOD, OTHER_GOOD)
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "invalid_pin"
    pins.verify_pin(identity, OTHER_GOOD)
    assert [kind for kind, _ in events] == ["pin_set", "pin_changed"]


# ---------------------------------------------------------------- AUTH-17 lockout


def _fail(identity: SessionIdentity, times: int) -> list[ApiError]:
    return [_error(lambda: pins.verify_pin(identity, OTHER_GOOD)) for _ in range(times)]


def test_auth_17_five_wrong_pins_lock_and_then_need_a_fresh_otp(user, clock, events):
    identity = _with_pin(user)
    errors = _fail(identity, 5)
    assert [e.error_code for e in errors] == ["invalid_pin"] * 4 + ["pin_locked"]
    assert errors[-1].status_code == 429 and errors[-1].extra["retry_after"] == 900
    assert events[-1] == ("pin_locked", {"level": 1})
    # The right PIN does not help during the lock.
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "pin_locked"
    clock.advance(901)
    error = _error(lambda: pins.verify_pin(identity, GOOD))
    assert (error.error_code, error.extra["factors"]) == ("step_up_required", ["otp", "pin"])
    identity = _fresh_otp(identity)
    pins.verify_pin(identity, GOOD)


def test_an_otp_from_before_the_lock_does_not_unlock(user, clock):
    identity = _fresh_otp(_with_pin(user))
    clock.advance(10)
    _fail(identity, 5)
    clock.advance(901)
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "step_up_required"


def test_auth_17_lockouts_escalate_and_reset_after_a_quiet_day(user, clock, settings):
    settings.PIN_DAILY_FAILURE_CAP = 100  # the daily cap has its own test
    identity = _with_pin(user)
    durations = []
    for _ in range(4):
        identity = _fresh_otp(identity)
        durations.append(_fail(identity, 5)[-1].extra["retry_after"])
        clock.advance(durations[-1] + 1)
    assert durations == [900, 3600, 86400, 86400]
    # Once 24 h pass after the last lock ended, the level starts again from the first step.
    clock.advance(24 * 3600 + 1)
    identity = _fresh_otp(identity)
    assert _fail(identity, 5)[-1].extra["retry_after"] == 900


def test_auth_17_daily_cap_holds_across_otp_unlocks(user, clock, events):
    identity = _with_pin(user)
    identity = _fresh_otp(identity)
    first = _fail(identity, 5)[-1]
    assert first.error_code == "pin_locked"
    clock.advance(901)
    identity = _fresh_otp(identity)  # an OTP gives a fresh run ...
    errors = _fail(identity, 5)
    # ... but only up to the cap of 10 wrong PINs a day: the 10th locks until midnight.
    assert errors[-1].error_code == "pin_locked"
    assert events[-1][0] == "pin_daily_cap"
    clock.advance(3601 * 2)
    identity = _fresh_otp(identity)
    error = _error(lambda: pins.verify_pin(identity, GOOD))
    assert error.error_code == "pin_locked"  # the right PIN, a fresh OTP: still capped today
    clock.advance(error.extra["retry_after"] + 1)  # past midnight Nairobi
    identity = _fresh_otp(identity)
    pins.verify_pin(identity, GOOD)


def test_every_lock_writes_a_security_event(user, clock, events):
    identity = _with_pin(user)
    _fail(identity, 5)
    clock.advance(901)
    identity = _fresh_otp(identity)
    _fail(identity, 5)
    assert [kind for kind, _ in events if kind != "pin_set"] == ["pin_locked", "pin_daily_cap"]


def test_wrong_attempts_are_committed_before_the_error(user):
    identity = _with_pin(user)
    _fail(identity, 2)
    assert _row(user).failed_attempts == 2 and _row(user).day_failures == 2


# ---------------------------------------------------------------- isolation


def test_pin_rows_are_isolated_per_user(user):
    _with_pin(user)
    other = User.objects.create_user("0712000999")
    with user_context(other.id):
        assert not StepUpPin.objects.exists()
    assert not StepUpPin.objects.exists()  # no user context: nothing
    assert pins.has_pin(user.id) and not pins.has_pin(other.id)


# ---------------------------------------------------------------- order, scope, robustness


def test_a_locked_pin_answers_before_any_hashing(user, clock, monkeypatch):
    identity = _with_pin(user)
    _fail(identity, 5)

    def no_hashing(*args, **kwargs):
        raise AssertionError("a locked PIN must not be hashed")

    monkeypatch.setattr(pins, "_hasher", no_hashing)
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "pin_locked"
    assert _error(lambda: pins.verify_pin(identity, OTHER_GOOD)).error_code == "pin_locked"
    clock.advance(901)  # lock over; without a fresh OTP the answer still needs no hash
    assert _error(lambda: pins.verify_pin(identity, GOOD)).error_code == "step_up_required"


def test_the_daily_cap_answers_before_any_hashing(user, clock, monkeypatch):
    identity = _with_pin(user)
    _fail(identity, 5)
    clock.advance(901)
    identity = _fresh_otp(identity)
    _fail(identity, 5)  # the daily cap
    monkeypatch.setattr(pins, "_hasher", lambda: (_ for _ in ()).throw(AssertionError()))
    assert _error(lambda: pins.verify_pin(_fresh_otp(identity), GOOD)).error_code == "pin_locked"


def test_pin_checks_are_rate_limited_per_session(user, settings, monkeypatch):
    settings.PIN_VERIFY_LIMIT_PER_SESSION = (3, 900)
    identity = _with_pin(user)
    pins.verify_pin(identity, GOOD)
    _fail(identity, 2)
    error = _error(lambda: pins.verify_pin(identity, GOOD))
    assert (error.error_code, error.status_code) == ("rate_limited", 429)
    assert 1 <= error.extra["retry_after"] <= 900
    assert _row(user).failed_attempts == 2  # refused before the row: nothing counted
    other = _reload(_fresh_otp(_identity(user, "install-0002")))
    pins.verify_pin(other, GOOD)  # another session has its own budget


def test_pin_checks_fail_closed_without_the_rate_limit_store(user, monkeypatch):
    identity = _with_pin(user)

    def down(*args, **kwargs):
        raise RateLimitUnavailable

    monkeypatch.setattr(pins, "hit", down)
    error = _error(lambda: pins.verify_pin(identity, GOOD))
    assert (error.error_code, error.status_code) == ("service_unavailable", 503)


def test_lock_state_is_per_user_so_a_new_session_does_not_reset_it(user, clock):
    identity = _with_pin(user)
    _fail(identity, 3)
    fresh_login = _identity(user, "install-0002")
    lefts = _error(lambda: pins.verify_pin(fresh_login, OTHER_GOOD)).extra["attempts_left"]
    assert lefts == 1  # the run continues across sessions
    assert _error(lambda: pins.verify_pin(fresh_login, OTHER_GOOD)).error_code == "pin_locked"
    third = _fresh_otp(_identity(user, "install-0003"))  # a new login and a fresh OTP ...
    assert _error(lambda: pins.verify_pin(third, GOOD)).error_code == "pin_locked"  # ... locked
    clock.advance(901)
    stale = _identity(user, "install-0004")
    clock.advance(301)  # this session's login is no OTP from after the lock
    assert _error(lambda: pins.verify_pin(stale, GOOD)).error_code == "step_up_required"


def test_a_failed_rehash_does_not_fail_a_correct_pin(user, clock, settings, monkeypatch, caplog):
    identity = _with_pin(user)
    before = _row(user).pin_hash
    settings.PIN_ARGON2_MEMORY_KIB = 16 * 1024  # the next success wants to re-hash
    monkeypatch.setattr(pins, "_hash", lambda user_id, pin: ("$" + "x" * 300, "k1"))  # too long
    clock.advance(60)
    pins.verify_pin(identity, GOOD)  # the write fails inside; the check still succeeds
    assert _reload(identity).step_up_pin_at == clock.now
    row = _row(user)
    assert (row.pin_hash, row.failed_attempts, row.day_failures) == (before, 0, 0)
    assert "pin_rehash_failed" in caplog.text and GOOD not in caplog.text
    monkeypatch.undo()
    pins.verify_pin(identity, GOOD)  # and the next success upgrades it
    assert extract_parameters(_row(user).pin_hash).memory_cost == 16 * 1024


def test_a_rehash_that_raises_does_not_count_as_a_wrong_attempt(user, settings, monkeypatch):
    identity = _with_pin(user)
    _fail(identity, 2)
    settings.PIN_ARGON2_MEMORY_KIB = 16 * 1024

    def broken(user_id, pin):
        raise MemoryError("argon2 could not allocate")

    monkeypatch.setattr(pins, "_hash", broken)
    pins.verify_pin(identity, GOOD)
    assert _row(user).failed_attempts == 0 and _row(user).day_failures == 2


def test_no_pin_hash_or_pepper_in_logs_or_security_events(user, clock, settings, caplog):
    caplog.set_level(logging.DEBUG)
    identity = _with_pin(user)
    pins.change_pin(identity, GOOD, OTHER_GOOD)
    _error(lambda: pins.change_pin(identity, "390417", GOOD))
    for _ in range(4):  # invalid_pin, then pin_locked with an event
        _error(lambda: pins.verify_pin(identity, "390417"))
    clock.advance(901)
    identity = _fresh_otp(identity)
    settings.PIN_ARGON2_MEMORY_KIB = 16 * 1024
    pins.verify_pin(identity, OTHER_GOOD)  # a re-hash
    with user_context(user.id):
        events = list(SecurityEvent.objects.filter(user_id=user.id).values("kind", "metadata"))
        stored = StepUpPin.objects.get(user_id=user.id).pin_hash
    assert {e["kind"] for e in events} >= {"pin_set", "pin_changed", "pin_locked"}
    pepper_values = [part.split(":", 1)[1] for part in settings.PIN_PEPPER_KEYS.split(",")]
    secrets = [GOOD, OTHER_GOOD, "390417", stored, stored.rsplit("$", 1)[-1], *pepper_values]
    written = caplog.text + repr(events)
    for secret in secrets:
        assert secret not in written
    assert all(set(e["metadata"]) <= {"level", "count"} for e in events)

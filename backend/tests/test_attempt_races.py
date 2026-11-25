"""Parallel guesses never exceed the attempt limits (AUTH-01 for OTP codes, AUTH-17 for the PIN).

Rows are committed for real so each thread uses its own connection; the row lock
(``SELECT ... FOR UPDATE`` around read, check and increment) makes the attempts queue up.
"""

import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts import otp, sessions
from apps.accounts import pin as pins
from apps.accounts.models import AuthSession, Device, OtpChallenge, OtpLockout, StepUpPin, User
from apps.accounts.sessions import DeviceInfo, SessionIdentity
from core.api_errors import ApiError
from core.tenancy import user_context

THREADS = 8


def _race(call: Callable[[], Any]) -> Counter[str]:
    """Run ``call`` in THREADS threads released together; count the outcomes by error code."""
    barrier = threading.Barrier(THREADS)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(10)
            call()
            outcome = "ok"
        except ApiError as exc:
            outcome = exc.error_code
        except Exception as exc:  # reported by the assertions
            outcome = repr(exc)
        finally:
            connection.close()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return Counter(outcomes)


def _owner_delete_pin(user_id: uuid.UUID) -> None:
    """Test cleanup only: the owner lifts FORCE RLS for one statement (the policy is for
    app_rw), deletes the committed row, and restores it, all in one transaction."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE accounts_steppin NO FORCE ROW LEVEL SECURITY")
        cursor.execute("DELETE FROM accounts_steppin WHERE user_id = %s", [user_id])
        cursor.execute("ALTER TABLE accounts_steppin FORCE ROW LEVEL SECURITY")


@pytest.fixture
def committed_pin(django_db_setup, django_db_blocker, as_owner, redis_db) -> Iterator[Any]:
    with django_db_blocker.unblock():
        user = User.objects.create_user("+254712999002")
        pair = sessions.login(user.id, DeviceInfo("install-race-pin", "android"), None)
        identity = sessions.authenticate_access(pair.access_token)
        sessions.mark_step_up(identity, "otp", timezone.now())
        identity = sessions.authenticate_access(pair.access_token)
        pins.set_first_pin(identity, "275931")
        yield user, sessions.authenticate_access(pair.access_token)
        with as_owner():  # app_rw may delete neither PIN rows nor sessions
            _owner_delete_pin(user.id)
            AuthSession.objects.filter(id=pair.session_id).delete()
        with user_context(user.id):
            Device.objects.filter(user_id=user.id).delete()
        User.objects.filter(id=user.id).delete()


def test_auth_17_parallel_wrong_pins_are_limited(committed_pin, django_db_blocker):
    user, identity = committed_pin
    assert isinstance(identity, SessionIdentity)
    with django_db_blocker.unblock():
        outcomes = _race(lambda: pins.verify_pin(identity, "802461"))
        # Exactly four counted answers and the fifth locks; everyone after is refused unhashed.
        assert outcomes == Counter({"invalid_pin": 4, "pin_locked": THREADS - 4}), outcomes
        with user_context(user.id):
            row = StepUpPin.objects.get(user_id=user.id)
        assert (row.day_failures, row.lock_level, row.needs_otp) == (5, 1, True)


@pytest.fixture
def committed_challenge(
    django_db_setup, django_db_blocker, as_owner, fake_senders
) -> Iterator[tuple[str, str]]:
    phone = "+254712999003"
    with django_db_blocker.unblock():
        sent = otp.request_otp(phone)
        code = str(fake_senders.outbox(phone)[0]["code"])
        yield str(sent.challenge_id), code
        with as_owner():
            OtpChallenge.objects.filter(phone=phone).delete()
            OtpLockout.objects.filter(phone=phone).delete()


def test_auth_01_parallel_wrong_codes_are_limited(committed_challenge, django_db_blocker):
    challenge_id, code = committed_challenge
    wrong = "000000" if code != "000000" else "111111"
    with django_db_blocker.unblock():
        outcomes = _race(lambda: otp.verify_otp(challenge_id, wrong))
        assert outcomes == Counter({"invalid_code": 4, "locked": THREADS - 4}), outcomes
        challenge = OtpChallenge.objects.get(id=challenge_id)
        assert challenge.attempts == 5
        assert OtpLockout.objects.get(phone=challenge.phone).level == 1


@pytest.fixture
def two_owners(django_db_setup, django_db_blocker, as_owner) -> Iterator[Any]:
    from apps.organizations.models import Chama, Membership
    from apps.organizations.roles import Role
    from core.tenancy import chama_context

    with django_db_blocker.unblock():
        chama = Chama.objects.create()
        people = []
        for n in (1, 2):
            user = User.objects.create_user(f"+25471299901{n}")
            with chama_context(chama.id):
                membership = Membership.objects.create(chama=chama, user=user, role=Role.CHAIR)
            pair = sessions.login(user.id, DeviceInfo(f"install-race-o{n}", "android"), chama.id)
            identity = sessions.authenticate_access(pair.access_token)
            sessions.mark_step_up(identity, "otp", timezone.now())
            people.append((user, membership, pair))
        yield chama, people
        with as_owner():
            AuthSession.objects.filter(user_id__in=[u.id for u, _, _ in people]).delete()
        for user, _, _ in people:
            with user_context(user.id):
                Device.objects.filter(user_id=user.id).delete()
        with as_owner(), chama_context(chama.id):
            Membership.objects.filter(chama_id=chama.id).delete()
            Chama.objects.filter(id=chama.id).delete()
        User.objects.filter(id__in=[u.id for u, _, _ in people]).delete()


def test_two_chairs_demoting_each_other_keep_one_chair(two_owners, django_db_blocker):
    """The chama row and the chair memberships are locked, so two chairs demoting each other
    at once cannot leave the chama without a chair."""
    from apps.organizations import members as staff
    from apps.organizations.models import Membership
    from apps.organizations.roles import Role
    from core.tenancy import chama_context

    chama, ((_, first, pair1), (_, second, pair2)) = two_owners
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def demote(pair: Any, target: Any) -> None:
        try:
            identity = sessions.authenticate_access(pair.access_token)
            barrier.wait(10)
            with chama_context(chama.id):
                staff.change_role(identity, Role.CHAIR, target.id, "treasurer")
            outcomes.append("ok")
        except Exception as exc:
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    with django_db_blocker.unblock():
        threads = [
            threading.Thread(target=demote, args=(pair1, second)),
            threading.Thread(target=demote, args=(pair2, first)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(outcomes) == ["ValidationError", "ok"], outcomes
        with chama_context(chama.id):
            owners = Membership.objects.filter(role=Role.CHAIR, status="active").count()
        assert owners == 1

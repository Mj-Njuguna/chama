"""Account models (internal to this module; other modules use ``apps.accounts.services``)."""

import uuid
from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.db import models

from core.phones import normalize_phone

# Stored phone numbers are always E.164 (invariant 3); the database refuses anything else.
E164_REGEX = r"^\+[1-9][0-9]{6,14}$"


class UserManager(BaseUserManager["User"]):
    def create_user(self, phone: str, **fields: Any) -> "User":
        """Create a user from raw phone input. Users never have a usable password."""
        user = self.model(phone=normalize_phone(phone), **fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, **fields: Any) -> "User":
        raise NotImplementedError("There are no password logins.")


class User(AbstractBaseUser):
    """A person, identified by phone number. One user may belong to several chamas.

    Outside chama RLS: a user exists before and across chamas. No passwords: the
    inherited ``password`` column always holds an unusable marker. No ``PermissionsMixin``: access
    is decided by the membership role (organizations module), never by Django permissions or groups.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Plaintext E.164 with a global unique index (: no HMAC column).
    phone = models.CharField(max_length=16, unique=True)
    display_name = models.CharField(max_length=100, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    objects = UserManager()

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(phone__regex=E164_REGEX), name="accounts_user_phone_e164"
            ),
            # No passwords: only Django's unusable marker may be stored.
            models.CheckConstraint(
                condition=models.Q(password__startswith=UNUSABLE_PASSWORD_PREFIX),
                name="accounts_user_password_unusable",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)  # never the phone number

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Every creation path ends unusable, not only create_user(); the CHECK backs this up for
        # paths that skip save() (bulk_create, raw SQL).
        if self.has_usable_password():
            self.set_unusable_password()
        super().save(*args, **kwargs)

    def set_password(self, raw_password: str | None) -> None:
        if raw_password is not None:
            raise ValueError("users have no passwords")
        super().set_password(None)


class SecurityEventKind(models.TextChoices):
    OTP_SEND_FAILED = "otp_send_failed"
    OTP_VERIFY_FAILED = "otp_verify_failed"
    OTP_CHALLENGE_LOCKED = "otp_challenge_locked"
    NUMBER_LOCKED = "number_locked"
    OTP_DAILY_CAP = "otp_daily_cap"
    OTP_COST_ALERT = "otp_cost_alert"
    LOGIN = "login"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    REFRESH_REUSED = "refresh_reused"
    DEVICE_REVOKED = "device_revoked"
    CHAMA_CREATED = "chama_created"
    CHAMA_SWITCHED = "chama_switched"
    # An operator revoked a compromised AUTH_TOKEN_KEYS key.
    TOKEN_KEY_REVOKED = "token_key_revoked"  # noqa: S105 (an event name, not a secret)
    # Server step-up PIN.
    PIN_SET = "pin_set"
    PIN_CHANGED = "pin_changed"
    PIN_LOCKED = "pin_locked"
    PIN_DAILY_CAP = "pin_daily_cap"
    # Cooling-off changes and the freeze.
    CHANGE_REQUESTED = "change_requested"
    CHANGE_APPLIED = "change_applied"
    CHANGE_CANCELLED = "change_cancelled"
    CHAMA_FROZEN = "chama_frozen"
    CHAMA_UNFROZEN = "chama_unfrozen"
    PIN_RESET_REQUESTED = "pin_reset_requested"
    PIN_RESET_COMPLETED = "pin_reset_completed"
    PIN_RESET_CANCELLED = "pin_reset_cancelled"
    # Members: the invitee or member is recorded as a keyed hash of their phone number.
    MEMBER_INVITED = "member_invited"
    ROSTER_IMPORTED = "roster_imported"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_DECLINED = "invitation_declined"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    MEMBER_REMOVED = "member_removed"
    CONSTITUTION_CHANGED = "constitution_changed"
    # Money recorded or reversed by a person. The receipt rows hold the details; the event
    # records who and when in the chama's audit trail.
    MANUAL_RECEIPT_RECORDED = "manual_receipt_recorded"
    RECEIPT_VOIDED = "receipt_voided"


class SecurityEvent(models.Model):
    """Append-only audit record of a security-relevant event.

    ``app_rw`` may only INSERT and SELECT (the migration revokes UPDATE and DELETE), and the
    three audit policies decide who reads what: chama rows in their chama's context, rows
    without a chama only by their own user. IDs are plain UUIDs without foreign keys: events
    outlive the sessions, devices and users they mention, and a cascading update or delete would
    need a privilege the app does not have.

    Never store a phone number, OTP, token or IP address here: ``subject_hash`` and ``ip_hash``
    are keyed HMACs (``core.keys``), and ``metadata`` is checked against a fixed schema
    (``apps.accounts.audit``).
    """

    Kind = SecurityEventKind

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    chama_id = models.UUIDField(null=True, blank=True)
    user_id = models.UUIDField(null=True, blank=True)
    device_id = models.UUIDField(null=True, blank=True)
    session_id = models.UUIDField(null=True, blank=True)
    # "<key id>:<hex>" keyed HMACs: correlate repeated attempts without storing the value.
    subject_hash = models.CharField(max_length=96, blank=True, default="")
    ip_hash = models.CharField(max_length=96, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(kind__in=SecurityEventKind.values),
                name="accounts_securityevent_kind_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["chama_id", "occurred_at"], name="accounts_se_chama_idx"),
            models.Index(fields=["user_id", "occurred_at"], name="accounts_se_user_idx"),
            models.Index(fields=["subject_hash", "occurred_at"], name="accounts_se_subject_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.id}"


class OtpChallengeStatus(models.TextChoices):
    PENDING = "pending"  # created, send in progress
    ACTIVE = "active"  # sent; the code may be verified
    SEND_FAILED = "send_failed"  # no channel accepted; a late delivery is useless
    SUPERSEDED = "superseded"  # a newer code was requested for the number
    LOCKED = "locked"  # too many wrong codes
    USED = "used"  # verified once; never again


class OtpChallenge(models.Model):
    """One code sent to one phone number. RLS-exempt: it exists before login.

    Only a keyed hash of the code is stored (``OTP_PEPPER_KEYS``). The phone number is
    stored because verifying a code must know whose it was; rows are short-lived.
    """

    Status = OtpChallengeStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=16)
    code_hash = models.CharField(max_length=96)
    status = models.CharField(
        max_length=16, choices=OtpChallengeStatus.choices, default=OtpChallengeStatus.PENDING
    )
    channel = models.CharField(max_length=16, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    # Estimated cost of the accepted send, KES minor units (OTP_ESTIMATED_COST_MINOR).
    cost_minor = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(phone__regex=E164_REGEX), name="accounts_otpchallenge_phone_e164"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=OtpChallengeStatus.values),
                name="accounts_otpchallenge_status_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["phone", "created_at"], name="accounts_otp_phone_idx"),
            models.Index(fields=["created_at"], name="accounts_otp_created_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class OtpLockout(models.Model):
    """Escalating lockout per phone number. RLS-exempt: before login.

    Applies to every number alike, registered or not, so it reveals nothing about chamas.
    """

    phone = models.CharField(max_length=16, primary_key=True)
    level = models.PositiveSmallIntegerField(default=0)  # lockouts so far in the current period
    locked_until = models.DateTimeField(null=True, blank=True)
    last_locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(phone__regex=E164_REGEX), name="accounts_otplockout_phone_e164"
            ),
        ]

    def __str__(self) -> str:
        return "lockout"


class DevicePlatform(models.TextChoices):
    ANDROID = "android"
    IOS = "ios"


class Device(models.Model):
    """One app installation of one user. Under user RLS (``user_isolation``): only
    its own user, in user context, may see or change it."""

    Platform = DevicePlatform

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    # The installation's own stable ID, generated by the app and kept in secure storage.
    installation_id = models.CharField(max_length=64)
    name = models.CharField(max_length=64, blank=True, default="")
    platform = models.CharField(max_length=16, choices=DevicePlatform.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["user", "installation_id"], name="accounts_device_installation_uniq"
            ),
            # Target of the composite FK from accounts_authsession (migration 0012): a
            # session's device must belong to the session's user.
            models.UniqueConstraint(fields=["id", "user"], name="accounts_device_id_user_uniq"),
            models.CheckConstraint(
                condition=models.Q(platform__in=DevicePlatform.values),
                name="accounts_device_platform_valid",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


class SessionRevokedReason(models.TextChoices):
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    REFRESH_REUSED = "refresh_reused"
    DEVICE_REVOKED = "device_revoked"
    KEY_UNAVAILABLE = "key_unavailable"
    USER_INACTIVE = "user_inactive"


class AuthSession(models.Model):
    """One login on one device: a refresh-token family. RLS-exempt: found by token
    hash before the user is known. Accessed only through ``apps.accounts.sessions``.

    ``user_id``/``device_id`` are plain columns (no ORM relation, so no other module can reach
    sessions through ``User`` or ``Device``), backed by database foreign keys with explicit
    ``ON DELETE`` rules added in migration 0012.

    Only SHA-256 hashes of tokens are stored. ``refresh_generation`` counts rotations;
    ``rotation_key_id`` names the AUTH_TOKEN_KEYS key that derived the current pair, so the
    grace window can derive it again.
    """

    RevokedReason = SessionRevokedReason

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField()
    device_id = models.UUIDField()
    active_chama_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    absolute_expires_at = models.DateTimeField()
    access_hash = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_hash = models.CharField(max_length=64, unique=True)
    refresh_expires_at = models.DateTimeField()
    refresh_generation = models.PositiveIntegerField(default=0)
    rotation_key_id = models.CharField(max_length=16, blank=True, default="")
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(
        max_length=32, choices=SessionRevokedReason.choices, blank=True, default=""
    )
    # Step-up freshness for this session: when the server PIN and a fresh OTP were
    # last verified. Freshness windows are settings; nothing else is kept.
    step_up_pin_at = models.DateTimeField(null=True, blank=True)
    step_up_otp_at = models.DateTimeField(null=True, blank=True)
    # When the login OTP behind this session was verified. Counts ONLY for setting the first
    # PIN (5 minutes); never as a step-up OTP for any action.
    login_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(revoked_reason__in=["", *SessionRevokedReason.values]),
                name="accounts_authsession_reason_valid",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["user_id", "revoked_at"], name="accounts_session_user_idx"),
            models.Index(fields=["device_id"], name="accounts_session_device_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class AuthToken(models.Model):
    """The hash of a retired refresh token: presenting it again is reuse, except the
    immediately previous one inside the grace window. RLS-exempt; ``apps.accounts.sessions``
    only. ``session_id`` has a database FK (``ON DELETE CASCADE``, migration 0012)."""

    token_hash = models.CharField(max_length=64, primary_key=True)
    session_id = models.UUIDField()
    generation = models.PositiveIntegerField()
    retired_at = models.DateTimeField()

    class Meta:
        indexes: ClassVar = [
            models.Index(fields=["session_id"], name="accounts_authtoken_session_idx"),
        ]

    def __str__(self) -> str:
        return "retired refresh token"


class PushTokenType(models.TextChoices):
    EXPO = "expo"
    FCM = "fcm"


class PushToken(models.Model):
    """A push token registered by one session on one device.

    Under user RLS (``user_isolation``) like ``Device``. Stored only: nothing is sent yet, and
    the token format is not checked beyond length because the Expo/FCM details are not yet
    verified. A token is usable only while its session is live, its device is
    not revoked and it is not revoked itself (``apps.accounts.devices.push_targets_for_user``).
    ``device_id`` and ``session_id`` are backed by database foreign keys (migration 0014).
    """

    TokenType = PushTokenType

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    device_id = models.UUIDField()
    session_id = models.UUIDField()
    token_type = models.CharField(max_length=16, choices=PushTokenType.choices)
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(token_type__in=PushTokenType.values),
                name="accounts_pushtoken_type_valid",
            ),
            # One live token per device: registering a new one revokes the previous.
            models.UniqueConstraint(
                fields=["device_id"],
                condition=models.Q(revoked_at__isnull=True),
                name="accounts_pushtoken_one_live_per_device",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["session_id"], name="accounts_pushtoken_session_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class StepUpPin(models.Model):
    """A user's server step-up PIN. Under user RLS (``user_isolation``).

    ``pin_hash`` is an Argon2id hash (its parameters are part of the encoded string) of an
    HMAC of the PIN under the ``PIN_PEPPER_KEYS`` key named in ``pepper_key_id``. The PIN itself
    is never stored. Read and written only by ``apps.accounts.pin``.

    Attempt state: ``failed_attempts`` counts wrong PINs since the last success or unlock; five
    lock the PIN with an escalating lockout (``lock_level``, ``locked_until``) and then require a
    fresh OTP (``needs_otp``). ``day``/``day_failures`` cap wrong PINs per Nairobi day, whatever
    unlocks happen in between.
    """

    user = models.OneToOneField(User, on_delete=models.PROTECT, primary_key=True, related_name="+")
    pin_hash = models.CharField(max_length=255)
    pepper_key_id = models.CharField(max_length=16)
    set_at = models.DateTimeField()
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    lock_level = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_locked_at = models.DateTimeField(null=True, blank=True)
    needs_otp = models.BooleanField(default=False)
    day = models.DateField(null=True, blank=True)
    day_failures = models.PositiveSmallIntegerField(default=0)
    # Forgot PIN: the requested new PIN waits out a cooling-off here. The current PIN
    # keeps working until the reset is completed, which updates this row (never a delete).
    reset_pin_hash = models.CharField(max_length=255, blank=True, default="")
    reset_pepper_key_id = models.CharField(max_length=16, blank=True, default="")
    reset_requested_at = models.DateTimeField(null=True, blank=True)
    reset_effective_at = models.DateTimeField(null=True, blank=True)
    reset_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_steppin"

    def __str__(self) -> str:
        return "step-up pin"

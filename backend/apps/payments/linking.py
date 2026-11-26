"""Payment linking: the chama's own paybill or till and its Daraja app credentials (internal).

- First linking: active at once (the request already passed the PAYMENT_LINKING step-up: fresh
  OTP, new-device wait, freeze).
- Replacing active details: a PENDING row plus an organizations cooling-off change. Officials
  are alerted; any official can cancel; after the cooling-off an allowed official applies it
  (``apply_linking``), which re-runs the policy with the chama locked, so a freeze pressed
  meanwhile wins.
- Secrets are write-only: encrypted at rest, never returned.
- Each chama gets ONE unguessable callback token for its provider URLs; only its SHA-256 hash
  is stored in clear (``CallbackRoute``), so a callback finds its chama without trusting the
  body.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.services import SessionIdentity
from apps.organizations.services import (
    PendingChangeKind,
    apply_change,
    open_changes,
    request_change,
)
from apps.payments.gateway import Credentials
from apps.payments.models import CallbackRoute, LinkingStatus, PaymentLinking
from core.api_errors import ApiError, state_conflict


def no_pending_linking() -> ApiError:
    return ApiError("not_found", "No replacement payment details are waiting.", status_code=404)


@dataclass(frozen=True)
class LinkingView:
    channel: str | None
    shortcode: str | None
    environment: str | None
    credentials_set: bool
    status: str  # not_linked | pending | active
    pending_change_id: uuid.UUID | None
    pending_shortcode: str | None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _callback_token(chama_id: uuid.UUID) -> str:
    """The chama's callback token: the existing one, or a new one with its route."""
    existing = PaymentLinking.objects.filter(chama_id=chama_id).order_by("created_at").first()
    if existing is not None:
        return str(existing.callback_token)
    token = secrets.token_urlsafe(32)
    CallbackRoute.objects.create(token_hash=token_hash(token), chama_id=chama_id)
    return token


def _open_change_ids(identity: SessionIdentity) -> set[uuid.UUID]:
    return {c.id for c in open_changes(identity)}


def _discard_stale_pending(identity: SessionIdentity) -> None:
    pending = PaymentLinking.objects.filter(status=LinkingStatus.PENDING).first()
    if pending is not None and pending.change_id not in _open_change_ids(identity):
        pending.status, pending.closed_at = LinkingStatus.DISCARDED, timezone.now()
        pending.save(update_fields=["status", "closed_at"])


def linking_view(identity: SessionIdentity) -> LinkingView:
    _discard_stale_pending(identity)
    active = PaymentLinking.objects.filter(status=LinkingStatus.ACTIVE).first()
    pending = PaymentLinking.objects.filter(status=LinkingStatus.PENDING).first()
    if active is None and pending is None:
        return LinkingView(None, None, None, False, "not_linked", None, None)
    shown = active if active is not None else pending
    if shown is None:  # unreachable: one of them exists
        raise RuntimeError("linking row vanished")
    return LinkingView(
        channel=shown.channel,
        shortcode=shown.shortcode,
        environment=shown.environment,
        credentials_set=True,
        status="active" if active is not None else "pending",
        pending_change_id=pending.change_id if pending else None,
        pending_shortcode=pending.shortcode if pending else None,
    )


def save_linking(
    identity: SessionIdentity,
    role: object,
    chama_id: uuid.UUID,
    *,
    channel: str,
    shortcode: str,
    environment: str,
    consumer_key: str,
    consumer_secret: str,
    passkey: str,
) -> LinkingView:
    with transaction.atomic():
        _discard_stale_pending(identity)
        active = PaymentLinking.objects.select_for_update().filter(status=LinkingStatus.ACTIVE)
        has_active = active.exists()
        change_id = None
        if has_active:
            # Raises cooling_off while another replacement is open, and re-checks the policy.
            change_id = request_change(identity, role, PendingChangeKind.PAYMENT_DETAILS).id
        try:
            with transaction.atomic():
                PaymentLinking.objects.create(
                    chama_id=chama_id,
                    status=LinkingStatus.PENDING if has_active else LinkingStatus.ACTIVE,
                    channel=channel,
                    shortcode=shortcode,
                    environment=environment,
                    consumer_key=consumer_key,
                    consumer_secret=consumer_secret,
                    passkey=passkey,
                    callback_token=_callback_token(chama_id),
                    change_id=change_id,
                    requested_by_user_id=identity.user_id,
                    activated_at=None if has_active else timezone.now(),
                )
        except IntegrityError as error:  # a concurrent first linking won
            raise state_conflict(
                "linking_changed", "Payment details changed meanwhile; reload."
            ) from error
        if not has_active:
            _register_urls_after_commit(chama_id)
    return linking_view(identity)


def apply_linking(identity: SessionIdentity, role: object, chama_id: uuid.UUID) -> LinkingView:
    with transaction.atomic():
        pending = (
            PaymentLinking.objects.select_for_update().filter(status=LinkingStatus.PENDING).first()
        )
        if pending is None or pending.change_id is None:
            raise no_pending_linking()
        apply_change(identity, role, pending.change_id)  # cooling-off, freeze, step-up
        now = timezone.now()
        PaymentLinking.objects.filter(status=LinkingStatus.ACTIVE).update(
            status=LinkingStatus.REPLACED, closed_at=now
        )
        pending.status, pending.activated_at = LinkingStatus.ACTIVE, now
        pending.save(update_fields=["status", "activated_at"])
        _register_urls_after_commit(chama_id)
    return linking_view(identity)


def _register_urls_after_commit(chama_id: uuid.UUID) -> None:
    from apps.payments.tasks import register_c2b_urls

    transaction.on_commit(lambda: register_c2b_urls.delay(chama_id=str(chama_id)))


def active_credentials() -> tuple[Credentials, str] | None:
    """The active details and the callback token of the current chama, or None."""
    row = PaymentLinking.objects.filter(status=LinkingStatus.ACTIVE).first()
    if row is None:
        return None
    creds = Credentials(
        channel=row.channel,
        shortcode=row.shortcode,
        environment=row.environment,
        consumer_key=row.consumer_key,
        consumer_secret=row.consumer_secret,
        passkey=row.passkey,
    )
    return creds, row.callback_token


def callback_url(token: str, path: str) -> str:
    base = settings.PAYMENTS_CALLBACK_BASE_URL.rstrip("/")
    return f"{base}/api/v1/payments/{path.format(token=token)}"


def chama_for_token(token: str) -> uuid.UUID | None:
    """The chama a callback token belongs to (RLS-exempt lookup by hash)."""
    if not token or len(token) > 128:
        return None
    return (
        CallbackRoute.objects.filter(token_hash=token_hash(token))
        .values_list("chama_id", flat=True)
        .first()
    )

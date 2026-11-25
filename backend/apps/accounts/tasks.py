"""Accounts background jobs."""

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import OtpChallenge, OtpLockout


@shared_task(name="apps.accounts.tasks.purge_otp_records")  # type: ignore[untyped-decorator]
def purge_otp_records() -> dict[str, int]:
    """Delete old challenges and lockouts that no longer matter.

    Both tables are RLS-exempt (they exist before login), so no chama context is needed.
    """
    now = timezone.now()
    cutoff = now - timedelta(hours=settings.OTP_CHALLENGE_RETENTION_HOURS)
    challenges, _ = OtpChallenge.objects.filter(created_at__lt=cutoff).delete()
    quiet = now - timedelta(seconds=settings.OTP_LOCKOUT_RESET_SECONDS)
    lockouts, _ = OtpLockout.objects.filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=now), last_locked_at__lt=quiet
    ).delete()
    return {"challenges": challenges, "lockouts": lockouts}

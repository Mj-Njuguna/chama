"""Members: invitations, roster import, roles and removal. Internal to organizations.

- **Invite** (officials, fresh OTP through the policy function): by phone number + name + role.
  The person gets an ``invited`` membership with their member reference code (a user row is
  created for a new number: users exist before memberships). Only the chair may invite someone
  straight into an official role. The invitation message goes out after commit through the
  notifications module; the invitee also sees the invitation in ``GET /me`` after signing in.
- **Roster import** (officials): a CSV with a header row and the columns ``name``, ``phone`` and
  optionally ``role``. ``preview`` checks every row and writes nothing (role check only, no
  step-up); ``commit`` invites every valid row in one transaction (same step-up as an invite)
  and reports the rows it skipped.
- **Accept / decline**: only the invited user, from their own session. Accepting records the
  invite terms (wording version) and the member's choice for the due-date STK prompt (on unless
  they turn it off).
- **Change role** (chair only, fresh OTP) and **remove** (officials, fresh OTP; removing an
  official needs the chair). At least one active chair at all times.
- The policy function runs again inside each write transaction with the chama row locked, so a
  freeze that lands after the permission check still wins. Every step writes a security event;
  phone numbers are recorded only as keyed hashes.
"""

import csv
import io
import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.services import (
    SecurityEventKind,
    SessionIdentity,
    phones_for,
    record_security_event,
    user_for_phone,
)
from apps.organizations.codes import new_member_code
from apps.organizations.errors import chama_not_available, member_not_found
from apps.organizations.models import Chama, Membership, MembershipStatus
from apps.organizations.permissions import Action, is_allowed
from apps.organizations.roles import OFFICIALS, Role
from apps.organizations.selectors import memberships_for_user
from apps.organizations.stepup import chama_state, decide
from core.phones import InvalidPhone, normalize_phone
from core.tenancy import chama_context, current_chama_id
from core.text import checked_label

ROSTER_NAME_MAX = 100
ROSTER_MAX_ROWS = 500
ROSTER_MAX_BYTES = 200_000


# ---------------------------------------------------------------- helpers


def _chama(identity: SessionIdentity) -> uuid.UUID:
    chama_id = current_chama_id()
    if chama_id is None or chama_id != identity.active_chama_id:
        raise chama_not_available()
    return chama_id


def _check_policy(identity: SessionIdentity, caller_role: object, action: Action) -> uuid.UUID:
    """Inside the caller's transaction: the policy again, with the chama row locked."""
    chama_id = _chama(identity)
    decision = decide(
        caller_role, action, identity, timezone.now(), chama_state(chama_id, identity, lock=True)
    )
    if decision.error is not None:
        raise decision.error
    return chama_id


def _event(
    kind: SecurityEventKind,
    identity: SessionIdentity,
    chama_id: uuid.UUID,
    phone: str | None = None,
    **metadata: object,
) -> None:
    record_security_event(
        kind,
        chama_id=chama_id,
        user_id=identity.user_id,
        device_id=identity.device_id,
        session_id=identity.session_id,
        phone_e164=phone,
        metadata=metadata or None,
    )


def _unique_code(chama_id: uuid.UUID, taken: set[str]) -> str:
    """A member code not yet used in the chama (the unique constraint backs this up)."""
    if not taken:
        taken.update(
            Membership.objects.filter(chama_id=chama_id).values_list("member_code", flat=True)
        )
    while True:
        code = new_member_code()
        if code not in taken:
            taken.add(code)
            return code


def _checked_role(raw: object, caller_role: object) -> Role:
    try:
        role = Role(str(raw or Role.MEMBER).strip().lower())
    except ValueError:
        raise ValidationError({"role": ["invalid"]}) from None
    if role != Role.MEMBER and caller_role != Role.CHAIR:
        raise PermissionDenied("Only the chair can make someone an official.")
    return role


def _send_invites_after_commit(chama_id: uuid.UUID, invites: list[tuple[str, str]]) -> None:
    from apps.organizations.tasks import send_invitations

    if invites:
        transaction.on_commit(lambda: send_invitations.delay(str(chama_id), invites))


# ---------------------------------------------------------------- invite


@dataclass(frozen=True)
class Invitation:
    membership_id: uuid.UUID
    name: str
    role: str
    status: str
    member_code: str


def _invite_locked(
    chama_id: uuid.UUID, phone: str, name: str, role: Role, taken: set[str]
) -> Membership | str:
    """Create or reopen the invited membership. Returns an error code instead when the number
    is already a member or invited. Runs in the chama context, inside a transaction."""
    user_id = user_for_phone(phone)
    membership = Membership.objects.select_for_update().filter(user_id=user_id).first()
    if membership is not None and membership.status != MembershipStatus.REVOKED:
        return "already_member"
    if membership is None:
        return Membership.objects.create(
            chama_id=chama_id,
            user_id=user_id,
            role=role,
            status=MembershipStatus.INVITED,
            member_code=_unique_code(chama_id, taken),
            roster_name=name,
        )
    # A former member: invited again on the same row with the same code (never deleted).
    membership.role, membership.status = role, MembershipStatus.INVITED
    membership.roster_name = name
    membership.terms_accepted_at, membership.joined_at = None, None
    membership.save(
        update_fields=["role", "status", "roster_name", "terms_accepted_at", "joined_at"]
    )
    return membership


def invite(
    identity: SessionIdentity, caller_role: object, phone: str, name: str, role: object
) -> Invitation:
    _chama(identity)
    try:
        phone = normalize_phone(phone)
    except InvalidPhone as exc:
        raise ValidationError({"phone": [exc.code.value]}) from None
    name = checked_label(name, "name", ROSTER_NAME_MAX, required=True)
    checked = _checked_role(role, caller_role)
    with transaction.atomic():
        chama_id = _check_policy(identity, caller_role, Action.MANAGE_MEMBERS)
        result = _invite_locked(chama_id, phone, name, checked, set())
        if isinstance(result, str):
            raise ValidationError({"phone": [result]})
        _event(SecurityEventKind.MEMBER_INVITED, identity, chama_id, phone, role=checked.value)
        _send_invites_after_commit(chama_id, [(phone, result.member_code)])
    return Invitation(result.id, name, checked.value, result.status, result.member_code)


# ---------------------------------------------------------------- roster import


@dataclass(frozen=True)
class RosterRow:
    row: int  # 1-based line number in the file, header included
    name: str
    phone: str  # E.164 when valid, otherwise what was typed
    role: str
    error: str | None = None


@dataclass(frozen=True)
class RosterResult:
    rows: list[RosterRow]
    valid: int
    invalid: int
    invited: int = 0
    skipped: list[RosterRow] = field(default_factory=list)


def _read_csv(text: str) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > ROSTER_MAX_BYTES:
        raise ValidationError({"csv": ["too_large"]})
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "name" not in headers or "phone" not in headers:
        raise ValidationError({"csv": ["missing_columns"]})
    reader.fieldnames = headers
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    if not rows:
        raise ValidationError({"csv": ["empty"]})
    if len(rows) > ROSTER_MAX_ROWS:
        raise ValidationError({"csv": ["too_many_rows"]})
    return rows


def _check_rows(raw_rows: list[dict[str, str]], caller_role: object) -> list[RosterRow]:
    """Every row checked on its own; numbers already in the chama are checked by the caller."""
    seen: set[str] = set()
    checked: list[RosterRow] = []
    for index, raw in enumerate(raw_rows, start=2):
        name, typed, role_raw = raw.get("name", ""), raw.get("phone", ""), raw.get("role", "")
        error: str | None = None
        phone = typed
        role = Role.MEMBER.value
        try:
            name = checked_label(name, "name", ROSTER_NAME_MAX, required=True)
        except ValidationError:
            error = "invalid_name"
        try:
            phone = normalize_phone(typed)
        except InvalidPhone:
            error = error or "invalid_phone"
        try:
            role = _checked_role(role_raw, caller_role).value
        except (ValidationError, PermissionDenied):
            error = error or "invalid_role"
        if error is None and phone in seen:
            error = "duplicate_in_file"
        seen.add(phone)
        checked.append(RosterRow(index, name, phone, role, error))
    return checked


def _mark_existing(rows: list[RosterRow]) -> list[RosterRow]:
    """Rows whose number is already an active or invited member get ``already_member``."""
    phones = [r.phone for r in rows if r.error is None]
    if not phones:
        return rows
    user_ids = Membership.objects.exclude(status=MembershipStatus.REVOKED).values_list(
        "user_id", flat=True
    )
    existing = set(phones_for(list(user_ids)).values())
    return [
        RosterRow(r.row, r.name, r.phone, r.role, "already_member")
        if r.error is None and r.phone in existing
        else r
        for r in rows
    ]


def _summary(rows: list[RosterRow], invited: int = 0) -> RosterResult:
    bad = [r for r in rows if r.error is not None]
    return RosterResult(rows, len(rows) - len(bad), len(bad), invited, bad)


def preview_roster(identity: SessionIdentity, caller_role: object, text: str) -> RosterResult:
    """Check a roster without writing anything. Officials (role check only)."""
    _chama(identity)
    if not is_allowed(caller_role, Action.MANAGE_MEMBERS):
        raise PermissionDenied("Your role does not allow this.")
    return _summary(_mark_existing(_check_rows(_read_csv(text), caller_role)))


def import_roster(identity: SessionIdentity, caller_role: object, text: str) -> RosterResult:
    """Invite every valid row in one transaction; invalid rows are reported and skipped."""
    rows = _check_rows(_read_csv(text), caller_role)
    with transaction.atomic():
        chama_id = _check_policy(identity, caller_role, Action.MANAGE_MEMBERS)
        rows = _mark_existing(rows)
        taken: set[str] = set()
        sent: list[tuple[str, str]] = []
        final: list[RosterRow] = []
        for row in rows:
            if row.error is not None:
                final.append(row)
                continue
            result = _invite_locked(chama_id, row.phone, row.name, Role(row.role), taken)
            if isinstance(result, str):
                final.append(RosterRow(row.row, row.name, row.phone, row.role, result))
                continue
            sent.append((row.phone, result.member_code))
            final.append(row)
        _event(SecurityEventKind.ROSTER_IMPORTED, identity, chama_id, count=min(len(sent), 9999))
        _send_invites_after_commit(chama_id, sent)
    return _summary(final, invited=len(sent))


# ---------------------------------------------------------------- accept / decline


def _answer(
    identity: SessionIdentity, chama_id: uuid.UUID, accept: bool, stk_opt_in: bool
) -> Membership:
    invited = [
        m
        for m in memberships_for_user(identity.user_id)
        if m.chama_id == chama_id and m.status == MembershipStatus.INVITED
    ]
    if not invited:
        raise chama_not_available()
    now = timezone.now()
    with chama_context(chama_id), transaction.atomic():
        membership = (
            Membership.objects.select_for_update()
            .filter(user_id=identity.user_id, status=MembershipStatus.INVITED)
            .first()
        )
        if membership is None:  # answered meanwhile
            raise chama_not_available()
        if accept:
            membership.status = MembershipStatus.ACTIVE
            membership.joined_at = now
            membership.terms_version = settings.INVITE_TERMS_VERSION
            membership.terms_accepted_at = now
            membership.scheduled_stk_opt_in = stk_opt_in
            kind = SecurityEventKind.INVITATION_ACCEPTED
        else:
            membership.status = MembershipStatus.REVOKED
            kind = SecurityEventKind.INVITATION_DECLINED
        membership.save(
            update_fields=[
                "status",
                "joined_at",
                "terms_version",
                "terms_accepted_at",
                "scheduled_stk_opt_in",
            ]
        )
        _event(kind, identity, chama_id, role=membership.role)
        if accept:
            from apps.notifications.services import publish_feed_event

            publish_feed_event(
                chama_id,
                "member_joined",
                {"member_id": str(membership.id), "member_name": membership.roster_name},
                key=f"member:{membership.id}:joined",
            )
        return membership


def accept(
    identity: SessionIdentity, chama_id: uuid.UUID, *, accept_terms: bool, stk_opt_in: bool
) -> Membership:
    """The invited user joins (accepting the invite terms). Switch to the chama to use it."""
    if not accept_terms:
        raise ValidationError({"accept_terms": ["required"]})
    return _answer(identity, chama_id, True, stk_opt_in)


def decline(identity: SessionIdentity, chama_id: uuid.UUID) -> None:
    _answer(identity, chama_id, False, False)


# ---------------------------------------------------------------- list, change, remove


@dataclass(frozen=True)
class Member:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    role: str
    status: str
    member_code: str
    phone: str | None  # officials only
    joined_at: object


def members(identity: SessionIdentity, caller_role: object) -> list[Member]:
    """The roster. Every member sees active members (no phone numbers); officials also see
    invitations and phone numbers."""
    _chama(identity)
    official = is_allowed(caller_role, Action.VIEW_INVITATIONS)
    rows = Membership.objects.exclude(status=MembershipStatus.REVOKED)
    if not official:
        rows = rows.filter(status=MembershipStatus.ACTIVE)
    found = list(
        rows.order_by("created_at", "id").values_list(
            "id", "user_id", "roster_name", "role", "status", "member_code", "joined_at"
        )
    )
    from apps.accounts.services import display_names

    user_ids = [r[1] for r in found]
    names = display_names(user_ids)
    phones = phones_for(user_ids) if official else {}
    return [
        Member(m, u, names.get(u) or roster or "", role, st, code, phones.get(u), joined)
        for m, u, roster, role, st, code, joined in found
    ]


def _locked_target(membership_id: object) -> Membership:
    try:
        parsed = uuid.UUID(str(membership_id))
    except ValueError:
        raise member_not_found() from None
    target = Membership.objects.select_for_update().filter(id=parsed).first()
    if target is None or target.status == MembershipStatus.REVOKED:
        raise member_not_found()
    return target


def _keep_a_chair(target: Membership, new_role: str | None) -> None:
    """Refuse if ``target`` is the last active chair (locks every active chair membership)."""
    if target.role != Role.CHAIR or target.status != MembershipStatus.ACTIVE:
        return
    if new_role == Role.CHAIR:
        return
    chairs = list(
        Membership.objects.select_for_update()
        .filter(role=Role.CHAIR, status=MembershipStatus.ACTIVE)
        .values_list("id", flat=True)
    )
    if [c for c in chairs if c != target.id] == []:
        raise ValidationError({"role": ["last_chair"]})


def _member(target: Membership) -> Member:
    phone = phones_for([target.user_id]).get(target.user_id)
    return Member(
        target.id,
        target.user_id,
        target.roster_name,
        target.role,
        target.status,
        target.member_code,
        phone,
        target.joined_at,
    )


def change_role(
    identity: SessionIdentity, caller_role: object, membership_id: object, role: object
) -> Member:
    try:
        new_role = Role(str(role))
    except ValueError:
        raise ValidationError({"role": ["invalid"]}) from None
    with transaction.atomic():
        chama_id = _check_policy(identity, caller_role, Action.CHANGE_ROLES)
        target = _locked_target(membership_id)
        _keep_a_chair(target, new_role)
        target.role = new_role
        target.save(update_fields=["role"])
        result = _member(target)
        _event(
            SecurityEventKind.MEMBER_ROLE_CHANGED,
            identity,
            chama_id,
            result.phone,
            role=new_role.value,
        )
    return result


def remove(identity: SessionIdentity, caller_role: object, membership_id: object) -> None:
    """Revoke a membership (or withdraw an invitation). Rows are kept."""
    with transaction.atomic():
        chama_id = _check_policy(identity, caller_role, Action.MANAGE_MEMBERS)
        target = _locked_target(membership_id)
        if target.role in OFFICIALS and caller_role != Role.CHAIR:
            raise PermissionDenied("Only the chair can remove an official.")
        _keep_a_chair(target, None)
        target.status = MembershipStatus.REVOKED
        target.save(update_fields=["status"])
        phone = phones_for([target.user_id]).get(target.user_id)
        _event(SecurityEventKind.MEMBER_REMOVED, identity, chama_id, phone, role=target.role)


def chama_name(chama_id: uuid.UUID) -> str:
    return Chama.objects.filter(id=chama_id).values_list("name", flat=True).first() or ""

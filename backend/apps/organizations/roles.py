"""Membership roles. The permission matrix lives in ``apps.organizations.permissions``."""

from django.db import models


class Role(models.TextChoices):
    CHAIR = "chair"
    TREASURER = "treasurer"
    SECRETARY = "secretary"
    MEMBER = "member"


OFFICIALS = frozenset({Role.CHAIR, Role.TREASURER, Role.SECRETARY})

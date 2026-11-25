"""Every sensitive (step-up) Action is reachable only through ``RequiresAction``.

Parsed statically, like tests/test_module_boundaries.py, so code paths no test executes are
covered too. A sensitive action may appear in application code only:

- in its own definition and policy (``apps/identity/permissions.py``, ``apps/identity/stepup.py``);
- as the argument of ``RequiresAction.for_(...)``.

``decide`` (the step-up policy) is called only by ``RequiresAction`` and, again inside the
action's own transaction with the chama row locked, by ``apps/identity/pending.py`` and
``apps/identity/staff.py``; the role
check that
other modules can import (``services.is_allowed``) refuses sensitive actions at run time.
"""

import ast
from pathlib import Path

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.organizations import services
from apps.organizations.permissions import Action, RequiresAction
from apps.organizations.roles import Role
from apps.organizations.stepup import STEP_UP

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = [ROOT / "apps", ROOT / "core", ROOT / "config"]
POLICY_FILES = {
    "apps/organizations/permissions.py",
    "apps/organizations/stepup.py",
    # The in-transaction re-check of each write names the action it re-checks.
    "apps/organizations/members.py",
    "apps/organizations/constitution.py",
}
# RequiresAction, and the in-transaction re-check of changes that write.
DECIDE_CALLERS = {
    "apps/organizations/permissions.py",
    "apps/organizations/pending.py",
    "apps/organizations/members.py",
    "apps/organizations/constitution.py",
}
SENSITIVE_NAMES = {action.name for action in STEP_UP}
SENSITIVE_VALUES = {action.value for action in STEP_UP}


def _sources() -> list[tuple[str, str]]:
    """``(path relative to backend/, source)`` for all application code."""
    return sorted(
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for directory in SOURCE_DIRS
        for path in directory.rglob("*.py")
        if "migrations" not in path.parts
    )


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _name(node: ast.expr) -> str | None:
    """``RequiresAction`` for both ``RequiresAction`` and ``services.RequiresAction``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _inside_requires_action(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    while node in parents:
        node = parents[node]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "for_"
            and _name(node.func.value) == "RequiresAction"
        ):
            return True
    return False


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def catalog_violations(files: list[tuple[str, str]]) -> list[str]:
    found = []
    for rel, source in files:
        tree = ast.parse(source, filename=rel)
        parents = _parents(tree)
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if (
                isinstance(node, ast.Attribute)
                and node.attr in SENSITIVE_NAMES
                and rel not in POLICY_FILES
                and not _inside_requires_action(node, parents)
            ):
                found.append(f"{where} uses Action.{node.attr} outside RequiresAction.for_")
            elif (
                isinstance(node, ast.Constant)
                and node.value in SENSITIVE_VALUES
                and rel not in POLICY_FILES
            ):
                found.append(f"{where} spells the sensitive action {node.value!r}")
            elif (
                isinstance(node, ast.Call)
                and _called_name(node) == "decide"
                and rel not in DECIDE_CALLERS
            ):
                found.append(f"{where} calls decide() outside RequiresAction")
    return found


def test_sensitive_actions_appear_only_in_requires_action():
    assert catalog_violations(_sources()) == []


def test_the_catalog_check_catches_a_bypass():
    """The check itself works: each kind of bypass is reported, the sanctioned use is not."""
    probe = (
        "from apps.accounts.services import Action, RequiresAction, is_allowed\n"
        "ok = RequiresAction.for_(Action.RECORD_RECEIPT)\n"
        "bad = is_allowed('owner', Action.EXPORT_DATA)\n"
        "spelled = 'payment_linking'\n"
        "decide(None, None, None, None)\n"
    )
    found = sorted(catalog_violations([("apps/billing/probe.py", probe)]))
    assert found == [
        "apps/billing/probe.py:3 uses Action.EXPORT_DATA outside RequiresAction.for_",
        "apps/billing/probe.py:4 spells the sensitive action 'payment_linking'",
        "apps/billing/probe.py:5 calls decide() outside RequiresAction",
    ]


@pytest.mark.parametrize("action", sorted(STEP_UP))
def test_the_exported_role_check_refuses_sensitive_actions(action):
    with pytest.raises(ValueError, match="RequiresAction"):
        services.is_allowed(Role.CHAIR, action)


def test_the_exported_role_check_still_answers_other_actions():
    assert services.is_allowed(Role.CHAIR, Action.VIEW_GROUP_TOTALS) is True
    assert services.is_allowed(Role.MEMBER, Action.VIEW_GROUP_TOTALS) is False
    assert services.is_allowed(Role.CHAIR, "unknown") is False


def _views(resolver: URLResolver) -> list[object]:
    found: list[object] = []
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            found.extend(_views(entry))
        elif isinstance(entry, URLPattern):
            found.append(getattr(entry.callback, "cls", None))
    return [view for view in found if view is not None]


@pytest.mark.parametrize("urlconf", ["config.urls", "tests.chama_probe_urls"])
def test_routed_views_with_a_sensitive_action_keep_drf_permission_checks(urlconf):
    """A routed view guarded by a sensitive action must not replace DRF's permission machinery
    (``get_permissions``/``check_permissions``/``initial``), which is what runs RequiresAction.
    No real endpoint has a sensitive action yet; the probes stand in until later milestones."""
    from rest_framework.views import APIView

    hooks = ("get_permissions", "check_permissions", "initial")
    guarded = 0
    for view in _views(get_resolver(urlconf)):
        guards = [
            p
            for p in getattr(view, "permission_classes", [])
            if isinstance(p, type) and issubclass(p, RequiresAction)
        ]
        if not any(g.action in STEP_UP for g in guards):
            continue
        guarded += 1
        assert isinstance(view, type) and issubclass(view, APIView), view
        for hook in hooks:
            assert getattr(view, hook) is getattr(APIView, hook), (view, hook)
    if urlconf.startswith("tests."):
        assert guarded >= len(STEP_UP)  # the check really ran over guarded views

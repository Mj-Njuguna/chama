""": modules talk only through each other's ``services`` and ``events``; ``core`` imports
no app. Imports are parsed statically, so this also covers code paths no test executes."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
APP_NAMES = sorted(p.name for p in APPS_DIR.iterdir() if (p / "apps.py").exists())
PUBLIC_SUBMODULES = {"services", "events"}


def _python_files(directory: Path) -> list[Path]:
    return [p for p in directory.rglob("*.py") if "migrations" not in p.parts]


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
            # "from apps.billing import models" imports a submodule by name.
            found.extend((node.lineno, f"{node.module}.{alias.name}") for alias in node.names)
    return found


def cross_app_violations(app: str, files: list[Path]) -> list[str]:
    violations = []
    for path in files:
        for lineno, module in _imported_modules(path):
            parts = module.split(".")
            # "apps" or "apps.<x>" alone is fine; the "apps.<x>.<name>" entry is what we check.
            if len(parts) < 3 or parts[0] != "apps" or parts[1] == app:
                continue
            if parts[2] not in PUBLIC_SUBMODULES:
                violations.append(f"{path.name}:{lineno} imports {module}")
    return violations


def test_modules_exist():
    assert (
        sorted(
            [
                "accounts",
                "organizations",
                "payments",
                "ledger",
                "contributions",
                "lending",
                "notifications",
                "reports",
            ]
        )
        == APP_NAMES
    )


@pytest.mark.parametrize("app", APP_NAMES)
def test_app_imports_only_public_interfaces_of_other_apps(app: str):
    assert cross_app_violations(app, _python_files(APPS_DIR / app)) == []


def test_core_imports_no_app():
    offenders = [
        f"{path.relative_to(ROOT)}:{lineno} imports {module}"
        for path in _python_files(ROOT / "core")
        for lineno, module in _imported_modules(path)
        if module == "apps" or module.startswith("apps.")
    ]
    assert offenders == []


def test_core_defines_no_models():
    """core owns no tables. Abstract bases (core.scope.PropertyScopedModel) are allowed."""
    from django.apps import apps

    assert list(apps.get_app_config("core").get_models()) == []


def test_checker_catches_internal_imports(tmp_path: Path):
    bad = tmp_path / "x.py"
    bad.write_text(
        "from apps.billing.models import Invoice\n"
        "from apps.ledger import services\n"
        "import apps.payments.selectors\n"
        "from apps.messaging import providers\n"
        "from apps.accounts.models import User\n",  # own app: allowed
        encoding="utf-8",
    )
    violations = cross_app_violations("accounts", [bad])
    assert violations == [
        "x.py:1 imports apps.billing.models",
        "x.py:1 imports apps.billing.models.Invoice",
        "x.py:3 imports apps.payments.selectors",
        "x.py:4 imports apps.messaging.providers",
    ]

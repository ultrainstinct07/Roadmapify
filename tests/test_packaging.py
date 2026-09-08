"""pyproject.toml must not promise anything the package does not have.

Every one of these was a real defect at 0.1.0: `roadmap-mcp` was a console
script for `roadmapify.serve`, a module that does not exist, so installing the
package put a binary on PATH that died with ModuleNotFoundError. Two of the
three package-data globs matched nothing and were silently dropped from the
wheel. Neither shows up in any other test, because neither is reachable from
inside the package.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

try:
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as _toml  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return _toml.load(fh)


def _scripts() -> list[tuple[str, str]]:
    return sorted(_pyproject().get("project", {}).get("scripts", {}).items())


@pytest.mark.parametrize("name,target", _scripts(), ids=lambda v: v)
def test_every_console_script_points_at_something_that_imports(name, target):
    """A console script for a missing module is not a latent bug: pip writes the
    launcher anyway, so the very first thing a new user runs tracebacks."""
    module, _, attr = target.partition(":")
    mod = importlib.import_module(module)
    assert hasattr(mod, attr), f"{name} = {target!r}, but {module} has no {attr!r}"
    assert callable(getattr(mod, attr))


def test_every_package_data_glob_matches_a_real_file():
    """A glob that matches nothing is dropped from the wheel without a warning,
    so a declaration reads as shipped data while shipping none."""
    data = _pyproject()["tool"]["setuptools"]["package-data"]
    for package, patterns in data.items():
        base = ROOT / package
        for pattern in patterns:
            assert list(base.glob(pattern)), f"{package}: {pattern!r} matches no file"


def test_the_declared_license_has_a_file():
    """PKG-INFO carries the expression either way; without the text, the licence
    claim is unbacked in every published artefact."""
    expression = _pyproject()["project"]["license"]
    assert isinstance(expression, str) and expression
    assert (ROOT / "LICENSE").is_file(), "license is declared but LICENSE is missing"


def test_the_packaged_modules_are_the_ones_on_disk():
    """`packages` is hand-listed; a new subpackage that is not added here is
    simply absent from the wheel, and only an install would reveal it."""
    declared = set(_pyproject()["tool"]["setuptools"]["packages"])
    on_disk = {p.parent.name for p in (ROOT / "roadmapify").rglob("__init__.py")}
    assert on_disk <= declared | {"roadmapify"}

"""ARCHITECTURE.md's module table must name real symbols.

Documentation that is only prose drifts silently. graphify guards its module
table by importing every symbol it names; this is the same trick. If you rename
something, this test fails until the doc is updated — which is the point.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parent.parent / "ARCHITECTURE.md"
_ROW_RE = re.compile(r"^\|\s*`([a-z_]+)\.py`\s*\|([^|]*)\|")
_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def _rows() -> list[tuple[str, list[str]]]:
    out = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line)
        if m:
            out.append((m.group(1), _SYMBOL_RE.findall(m.group(2))))
    return out


def test_the_table_is_not_empty():
    rows = _rows()
    assert len(rows) >= 8, f"only found {len(rows)} module rows — did the table move?"


@pytest.mark.parametrize("module,symbols", _rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_documented_symbol_exists(module, symbols):
    mod = importlib.import_module(f"roadmapify.{module}")
    missing = [s for s in symbols if not hasattr(mod, s)]
    assert not missing, f"ARCHITECTURE.md names {missing} in {module}.py, which does not have them"
    assert symbols, f"the row for {module}.py names no entry points"


def test_the_exit_code_table_matches_the_code():
    from roadmapify import cli

    text = DOC.read_text(encoding="utf-8")
    assert f"| {cli.EXIT_REJECTED} | already rejected" in text
    assert f"| {cli.EXIT_CONSTRAINT} | violates a recorded constraint" in text


def test_the_documented_floors_match_the_code():
    from roadmapify import textmatch

    text = DOC.read_text(encoding="utf-8")
    assert f"({textmatch.MATCH_FLOOR:.2f}) blocks" in text
    assert f"({textmatch.RELATED_FLOOR:.2f})" in text

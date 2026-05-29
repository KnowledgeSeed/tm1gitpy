import json
import sys

import pytest

from tm1_git_py.main import _load_filter_rules, _resolve_filter_rules
from tm1_git_py.services.filter import FilterRules, apply_default_filter_rules


def test_resolve_filter_rules_inline_comma():
    rules = _resolve_filter_rules("Cubes('A'),Dimensions('B')")
    assert isinstance(rules, FilterRules)
    assert "Cubes('A')" in rules._normalized_rules
    assert "Dimensions('B')" in rules._normalized_rules


def test_resolve_filter_rules_text_file(tmp_path):
    path = tmp_path / "filter.txt"
    path.write_text("Cubes('Sales*')\n# comment\n", encoding="utf-8")
    rules = _resolve_filter_rules(f"file://{path}")
    assert rules is not None
    assert "Cubes('Sales*')" in rules._normalized_rules


def test_resolve_filter_rules_tm1project_file(tmp_path):
    path = tmp_path / "tm1project.json"
    path.write_text(
        json.dumps({"Version": "1.0", "Ignore": ["Cubes/Views"]}),
        encoding="utf-8",
    )
    rules = _resolve_filter_rules(f"file://{path}")
    expected = apply_default_filter_rules(FilterRules(["Cubes/Views"]))
    assert rules._normalized_rules == expected._normalized_rules


def test_load_filter_rules_tm1project_returns_ignore_only(tmp_path):
    path = tmp_path / "tm1project.json"
    path.write_text(
        json.dumps({"Version": "1.0", "Ignore": ["Cubes/Views", "!Cubes('A')"]}),
        encoding="utf-8",
    )
    lines = _load_filter_rules(f"file://{path}")
    assert lines == ["Cubes/Views", "!Cubes('A')"]


def test_resolve_filter_rules_missing_file_exits(tmp_path, monkeypatch):
    path = tmp_path / "missing.txt"
    monkeypatch.setattr(sys, "exit", lambda code=1: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        _resolve_filter_rules(f"file://{path}")

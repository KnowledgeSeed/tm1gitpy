import json

import pytest

from tm1_git_py.model.tm1_project_json import Tm1ProjectJson
from tm1_git_py.services.filter import FilterRules, apply_default_filter_rules


@pytest.fixture
def sample_tm1project(tmp_path):
    path = tmp_path / "tm1project.json"
    path.write_text(
        json.dumps(
            {
                "Version": "1.0",
                "Ignore": [
                    "Cubes/Views",
                    "!Cubes('Cube_A')",
                    "Dimensions('Dim*')",
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_from_path_loads_ignore(sample_tm1project):
    project = Tm1ProjectJson.from_path(sample_tm1project)
    assert project.version == "1.0"
    assert project.ignore == (
        "Cubes/Views",
        "!Cubes('Cube_A')",
        "Dimensions('Dim*')",
    )


def test_is_tm1project_path_by_filename(tmp_path):
    path = tmp_path / "tm1project.json"
    path.write_text('{"Version": "1.0"}', encoding="utf-8")
    assert Tm1ProjectJson.is_tm1project_path(path)


def test_is_tm1project_path_by_version_key(tmp_path):
    path = tmp_path / "project.json"
    path.write_text('{"Version": "1.0", "Ignore": []}', encoding="utf-8")
    assert Tm1ProjectJson.is_tm1project_path(path)


def test_is_tm1project_path_false_for_filter_txt(tmp_path):
    path = tmp_path / "filter.txt"
    path.write_text("Cubes('Sales*')\n", encoding="utf-8")
    assert not Tm1ProjectJson.is_tm1project_path(path)


def test_to_filter_rules_applies_defaults(sample_tm1project):
    project = Tm1ProjectJson.from_path(sample_tm1project)
    rules = project.to_filter_rules()
    expected = apply_default_filter_rules(
        FilterRules(["Cubes/Views", "!Cubes('Cube_A')", "Dimensions('Dim*')"])
    )
    assert rules._normalized_rules == expected._normalized_rules


def test_from_dict_requires_version():
    with pytest.raises(ValueError, match="Version"):
        Tm1ProjectJson.from_dict({"Ignore": []})

import logging

from tests.unit_common import *
from tm1_git_py.model.cube import update_cube
from tm1_git_py.model.rule import merge_rule_text, parse_rules
from tm1_git_py.services.changeset_status import ChangeSetStatusStore


SERVER_TEXT = (
    "SKIPCHECK;\r\n"
    "#region A\r\n['a']=N:1;\r\n#endregion\r\n"
    "\r\n"
    "#region B\r\n['b']=N:1;\r\n#endregion\r\n"
    "FEEDERS;\r\n"
)
REGION_A2 = "#region A\r\n['a']=N:2;\r\n#endregion\r\n"


def _server(mocker, rule_text: str, dimensions=("Versions",)):
    tm1_service = mocker.Mock()
    tm1_service.cubes.get.return_value = types.SimpleNamespace(
        name="Sales",
        dimensions=list(dimensions),
        has_rules=bool(rule_text),
        rules=types.SimpleNamespace(body=rule_text),
    )
    return tm1_service


def _segment(name: str, text: str) -> Rule:
    return Rule(area=f"[{name}]", full_statement=text, name=name)


def _rule_change(change_type: ChangeType, cube_name: str, rule: Rule, apply: bool = True) -> Change:
    return Change(
        change_type=change_type,
        object_type=ObjectType.RULE,
        uri=rule.uri(cube_name),
        body=rule,
        apply=apply,
    )


def _cube_change(change_type: ChangeType, cube: Cube, apply: bool = True) -> Change:
    return Change(
        change_type=change_type,
        object_type=ObjectType.CUBE,
        uri=cube.uri(),
        body=cube,
        apply=apply,
    )


class TestMergeRuleText:

    def test_modify_one_region_keeps_other_text_verbatim(self):
        new_region = _segment("region_0002", "\r\n#region B\r\n['b']=N:2;\r\n#endregion\r\n")

        result = merge_rule_text(SERVER_TEXT, "Sales", [new_region])

        assert result == SERVER_TEXT.replace("['b']=N:1;", "['b']=N:2;")

    def test_add_and_remove_render_in_segment_order(self):
        added = _segment("region_0003", "#region C\r\n['c']=N:1;\r\n#endregion\r\n")
        removed = _segment("region_0001", "")

        result = merge_rule_text(SERVER_TEXT, "Sales", [added, removed])

        assert result == (
            "SKIPCHECK;\r\n"
            "\r\n#region B\r\n['b']=N:1;\r\n#endregion\r\n"
            "#region C\r\n['c']=N:1;\r\n#endregion\r\n"
            "FEEDERS;\r\n"
        )

    def test_same_segment_returns_target_text(self):
        same_region = parse_rules(SERVER_TEXT, "Sales")[1]

        assert merge_rule_text(SERVER_TEXT, "Sales", [same_region]) == SERVER_TEXT

    def test_removing_all_segments_gives_empty_text(self):
        removals = [_segment(rule.name, "") for rule in parse_rules(SERVER_TEXT, "Sales")]

        assert merge_rule_text(SERVER_TEXT, "Sales", removals) == ""

    def test_region_change_on_unmarked_target_is_rejected(self):
        region = _segment("region_0001", REGION_A2)

        with pytest.raises(ValueError, match="mix the unmarked 'default' rule"):
            merge_rule_text("SKIPCHECK;\r\n['a']=N:1;\r\n", "Sales", [region])

    def test_region_changes_replace_unmarked_target_when_default_removed(self):
        preamble = _segment("preamble", "SKIPCHECK;\r\n")
        region = _segment("region_0001", REGION_A2)

        result = merge_rule_text(
            "SKIPCHECK;\r\n['a']=N:1;\r\n",
            "Sales",
            [preamble, region, _segment("default", "")],
        )

        assert result == "SKIPCHECK;\r\n" + REGION_A2

    def test_unknown_rule_name_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported rule name 'subrule_x'"):
            merge_rule_text(SERVER_TEXT, "Sales", [_segment("subrule_x", "x")])


class TestUpdateCube:

    def test_writes_merged_text_once_without_strip(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)
        trailer = _segment("trailer", "FEEDERS;\r\n['a']=>['b'];\r\n\r\n")
        cube = Cube(name="Sales", dimensions=[], rules=[trailer], views=[])

        update_cube(tm1_service, cube)

        tm1_service.cubes.update_or_create_rules.assert_called_once_with(
            cube_name="Sales",
            rules=SERVER_TEXT.replace("FEEDERS;\r\n", "FEEDERS;\r\n['a']=>['b'];\r\n\r\n"),
        )
        tm1_service.cubes.update.assert_not_called()

    def test_unchanged_rules_skip_write(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)
        cube = Cube(name="Sales", dimensions=[], rules=[parse_rules(SERVER_TEXT, "Sales")[1]], views=[])

        response = update_cube(tm1_service, cube)

        assert response.ok
        tm1_service.cubes.update_or_create_rules.assert_not_called()

    def test_empty_delta_does_not_touch_rules(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)

        response = update_cube(tm1_service, Cube(name="Sales", dimensions=[], rules=[], views=[]))

        assert response.ok
        tm1_service.cubes.update_or_create_rules.assert_not_called()
        tm1_service.cubes.update.assert_not_called()

    def test_dimension_set_change_is_only_reported(self, mocker, caplog):
        tm1_service = _server(mocker, SERVER_TEXT, dimensions=("Versions",))
        cube = Cube(name="Sales", dimensions=["Versions", "Periods"], rules=[], views=[])

        with caplog.at_level(logging.WARNING, logger="tm1_git_py.model.cube"):
            update_cube(tm1_service, cube)

        assert "not supported and is not applied" in caplog.text
        tm1_service.cubes.update_or_create_rules.assert_not_called()

    def test_reordered_dimensions_are_not_reported(self, mocker, caplog):
        tm1_service = _server(mocker, SERVER_TEXT, dimensions=("Versions", "Periods"))
        cube = Cube(name="Sales", dimensions=["Periods", "Versions"], rules=[], views=[])

        with caplog.at_level(logging.WARNING, logger="tm1_git_py.model.cube"):
            update_cube(tm1_service, cube)

        assert "not supported" not in caplog.text


class TestApplyFoldsRuleChanges:

    def test_region_changes_of_one_cube_are_written_once(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)
        changeset = Changeset(changeset_id="20260929000001")
        changeset.changes = [
            _rule_change(ChangeType.MODIFY, "Sales", _segment("region_0001", REGION_A2)),
            _rule_change(ChangeType.REMOVE, "Sales", _segment("region_0002", "")),
        ]

        success, applied = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        assert len(applied) == 2
        tm1_service.cubes.update_or_create_rules.assert_called_once_with(
            cube_name="Sales",
            rules="SKIPCHECK;\r\n" + REGION_A2 + "FEEDERS;\r\n",
        )

    def test_unticked_region_keeps_target_text(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)
        changeset = Changeset(changeset_id="20260929000002")
        changeset.changes = [
            _rule_change(ChangeType.MODIFY, "Sales", _segment("region_0001", REGION_A2), apply=False),
            _rule_change(ChangeType.REMOVE, "Sales", _segment("region_0002", "")),
        ]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        tm1_service.cubes.update_or_create_rules.assert_called_once_with(
            cube_name="Sales",
            rules="SKIPCHECK;\r\n#region A\r\n['a']=N:1;\r\n#endregion\r\nFEEDERS;\r\n",
        )

    def test_created_cube_gets_only_selected_regions(self, mocker):
        tm1_service = mocker.Mock()
        tm1_cube = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        preamble = _segment("preamble", "SKIPCHECK;\r\n")
        region_a = _segment("region_0001", REGION_A2)
        region_b = _segment("region_0002", "#region B\r\n['b']=N:1;\r\n#endregion\r\n")
        new_cube = Cube(name="Sales", dimensions=["Versions"], rules=[preamble, region_a, region_b], views=[])
        changeset = Changeset(changeset_id="20260929000003")
        changeset.changes = [
            _cube_change(ChangeType.ADD, new_cube),
            _rule_change(ChangeType.ADD, "Sales", region_b),
            _rule_change(ChangeType.ADD, "Sales", region_a, apply=False),
            _rule_change(ChangeType.ADD, "Sales", preamble),
        ]

        success, applied = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        assert len(applied) == 3
        tm1_cube.assert_called_once_with(
            "Sales", ["Versions"], "SKIPCHECK;\r\n#region B\r\n['b']=N:1;\r\n#endregion\r\n"
        )
        tm1_service.cubes.create.assert_called_once()
        tm1_service.cubes.update_or_create_rules.assert_not_called()

    def test_dimension_only_cube_modify_does_not_touch_rules(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT, dimensions=("Versions",))
        source_cube = Cube(
            name="Sales",
            dimensions=["Versions", "Periods"],
            rules=parse_rules(SERVER_TEXT.replace("N:1", "N:9"), "Sales"),
            views=[],
        )
        changeset = Changeset(changeset_id="20260929000004")
        changeset.changes = [_cube_change(ChangeType.MODIFY, source_cube)]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        tm1_service.cubes.update_or_create_rules.assert_not_called()
        tm1_service.cubes.update.assert_not_called()

    def test_cube_modify_carries_region_delta_and_rows_share_result(self, mocker, tmp_path):
        tm1_service = _server(mocker, SERVER_TEXT)
        source_cube = Cube(
            name="Sales",
            dimensions=["Versions"],
            rules=parse_rules(SERVER_TEXT, "Sales"),
            views=[],
        )
        changeset = Changeset(changeset_id="20260929000005")
        changeset.changes = [
            _cube_change(ChangeType.MODIFY, source_cube),
            _rule_change(ChangeType.ADD, "Sales", _segment("region_0003", "#region C\r\n#endregion\r\n")),
            _rule_change(ChangeType.REMOVE, "Sales", _segment("trailer", "")),
        ]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, status_dir=tmp_path)

        assert success
        tm1_service.cubes.update_or_create_rules.assert_called_once_with(
            cube_name="Sales",
            rules=SERVER_TEXT.replace("FEEDERS;\r\n", "#region C\r\n#endregion\r\n"),
        )
        status = ChangeSetStatusStore.load(tmp_path, changeset.last_execution_id)
        assert status.total_operations == 3
        assert [(op.object_type, op.uri) for op in status.operations] == [
            ("Rule", "Cubes('Sales')/Rules('region_0003')"),
            ("Cube", "Cubes('Sales')"),
            ("Rule", "Cubes('Sales')/Rules('trailer')"),
        ]
        assert all(op.ok for op in status.operations)

    def test_stored_changeset_is_unchanged_after_apply(self, mocker):
        tm1_service = _server(mocker, SERVER_TEXT)
        source_cube = Cube(
            name="Sales",
            dimensions=["Versions"],
            rules=parse_rules(SERVER_TEXT, "Sales"),
            views=[],
        )
        changeset = Changeset(changeset_id="20260929000006")
        changeset.changes = [
            _cube_change(ChangeType.MODIFY, source_cube),
            _rule_change(ChangeType.MODIFY, "Sales", _segment("region_0001", REGION_A2)),
        ]
        before = [(change.uri, change.body.to_dict()) for change in changeset.changes]

        apply(tm1_service=tm1_service, changeset=changeset)

        assert [(change.uri, change.body.to_dict()) for change in changeset.changes] == before
        assert len(source_cube.rules) == 4

    def test_rule_changes_of_removed_cube_are_skipped(self, mocker):
        tm1_service = mocker.Mock()
        old_cube = Cube(name="Sales", dimensions=["Versions"], rules=parse_rules(SERVER_TEXT, "Sales"), views=[])
        changeset = Changeset(changeset_id="20260929000007")
        changeset.changes = [
            _cube_change(ChangeType.REMOVE, old_cube),
            _rule_change(ChangeType.REMOVE, "Sales", _segment("region_0001", "")),
        ]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        tm1_service.cubes.delete.assert_called_once_with("Sales")
        tm1_service.cubes.update_or_create_rules.assert_not_called()

    def test_rule_change_of_unselected_created_cube_fails(self, mocker):
        tm1_service = mocker.Mock()
        region = _segment("region_0001", REGION_A2)
        new_cube = Cube(name="Sales", dimensions=["Versions"], rules=[region], views=[])
        changeset = Changeset(changeset_id="20260929000008")
        changeset.changes = [
            _cube_change(ChangeType.ADD, new_cube, apply=False),
            _rule_change(ChangeType.ADD, "Sales", region),
        ]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset)

        assert not success
        tm1_service.cubes.create.assert_not_called()
        tm1_service.cubes.update_or_create_rules.assert_not_called()

    def test_default_rule_change_keeps_per_change_path(self, mocker):
        tm1_service = mocker.Mock()
        changeset = Changeset(changeset_id="20260929000009")
        changeset.changes = [
            _rule_change(ChangeType.MODIFY, "Sales", _segment("default", "[] = N: 1;")),
        ]

        success, _ = apply(tm1_service=tm1_service, changeset=changeset)

        assert success
        tm1_service.cubes.get.assert_not_called()
        tm1_service.cubes.update_or_create_rules.assert_called_once_with(
            cube_name="Sales",
            rules="[] = N: 1;",
        )

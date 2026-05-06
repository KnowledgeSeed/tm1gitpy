from tests.unit_common import *


class TestChangesetFiltering:

    def test_filter_changeset_removes_parent_and_children_across_sections(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        subset_mod_old = Subset(
            name="SubsetMod",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        subset_mod_new = Subset(
            name="SubsetMod",
            expression="{[MockDim].[MockHier].[E1]}",
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                uri=Hierarchy.uri_for("MockDim", "MockHier"),
                body=hier_new
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetMod"),
                body=subset_mod_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=Process.uri_for("KeepProcess"),
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": [Dimension.uri_for("MockDim")],
                "modify": [
                    Hierarchy.uri_for("MockDim", "MockHier")
                ],
            },
            filter_children=True
        )

        assert [obj.body.name for obj in filtered.changes] == ["SubsetA", "KeepProcess"]



    def test_filter_changeset_keeps_parent_when_only_child_matches(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )

        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [Subset.uri_for("MockDim", "MockHier", "SubsetA")],
                "remove": [],
                "modify": [],
            }
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        assert [obj.name for obj in filtered_adds] == ["MockDim"]


    def test_filter_changeset_does_not_remove_children_when_filter_children_false(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                uri=Hierarchy.uri_for("MockDim", "MockHier"),
                body=hier_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=Process.uri_for("KeepProcess"),
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": [Dimension.uri_for("MockDim")],
                "modify": [],
            },
            filter_children=False
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        filtered_mods = [c.body for c in filtered.changes if c.change_type == ChangeType.MODIFY]
        filtered_rems = [c.body for c in filtered.changes if c.change_type == ChangeType.REMOVE]

        assert [obj.name for obj in filtered_adds] == ["SubsetA", "KeepProcess"]
        assert len(filtered_mods) == 1
        assert [obj.name for obj in filtered_rems] == []

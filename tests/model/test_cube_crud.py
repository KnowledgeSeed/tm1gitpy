from tests.unit_common import *


class TestCubeCRUD:

    def test_create_cube_builds_tm1py_cube_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        cube_mock = make_cube(
            name="Cube_A",
            dimension_names=["Version", "Period", "Channel"],
        )

        tm1py_cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        tm1py_cube_instance = tm1py_cube_cls.return_value
        tm1_service.cubes.create.return_value = "create-result"

        result = cube.create_cube(tm1_service, cube_mock)

        expected_dims = ["Version", "Period", "Channel"]
        expected_rule_text = cube_mock.get_rule_text()

        tm1py_cube_cls.assert_called_once_with(
            cube_mock.name,
            expected_dims,
            expected_rule_text,
        )
        tm1_service.cubes.create.assert_called_once_with(tm1py_cube_instance)
        assert result == "create-result"


    def test_delete_cube_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.cubes.delete.return_value = "delete-result"
        cube_name = "Cube_To_Delete"
        cube_obj = make_cube(name=cube_name)

        result = cube.delete_cube(tm1_service, cube_obj)

        tm1_service.cubes.delete.assert_called_once_with(cube_name)
        assert result == "delete-result"


    @pytest.mark.skip
    def test_update_cube_updates_rules_when_views_same(self, mocker):
        tm1_service = mocker.Mock()

        dim_names = ["Version", "Period"]

        view = MDXView(
            name="ViewSame",
            mdx="SELECT FROM [Cube_B]",
            source_path="/views/Cube_B/ViewSame.json",
        )
        views = [view]

        rules_old = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 1;",
                comment="// old",
            )
        ]
        rules_new = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 2;",
                comment="// new",
            )
        ]

        cube_old = make_cube("Cube_B", dim_names, rules_old, views)
        cube_new = make_cube("Cube_B", dim_names, rules_new, views)

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="some different rules")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        # ACT
        result = cube.update_cube(tm1_service, payload)

        # Rules updated
        new_rule_text = cube_new.get_rule_text()
        assert cube_obj.rules._text == new_rule_text

        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_update_cube_reorders_dimensions_when_order_changes_only(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Cube_Order", ["A", "B", "C"])
        cube_new = make_cube("Cube_Order", ["B", "C", "A"])

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        result = cube.update_cube(tm1_service, payload)

        # --- Assertions on dimension reordering logic ---
        tm1_service.cubes.get.assert_called_once_with("Cube_Order")

        # Because order changed but set is the same, we must reorder storage dims
        tm1_service.cubes.update_storage_dimension_order.assert_called_once_with(
            cube_name="Cube_Order",
            dimension_names=["B", "C", "A"],
        )

        # Rules should not change (both empty), so no extra logic beyond update()
        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_add_dimension_to_cube_uses_first_leaf_and_copies_via_temp_cube(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year", "Region"]

        # --- TM1 mocks ---

        # Hierarchy with one consolidated + one leaf
        hier = mocker.Mock()
        consolidated = mocker.Mock()
        consolidated.name = "Total"
        consolidated.element_type = "Consolidated"
        leaf = mocker.Mock()
        leaf.name = "Leaf1"
        leaf.element_type = "Numeric"
        hier.elements.values.return_value = [consolidated, leaf]
        tm1_service.hierarchies.get.return_value = hier

        # Patch TM1py.Cube
        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")

        # Patch bedrock copy
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        # Patch create/delete cube wrappers
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        # create_dimension / element.create_element should NOT be called here
        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        # --- ACT ---
        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) default element: first leaf, no new element created
        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Region",
            hierarchy_name="Region",
        )
        create_dimension_mock.assert_not_called()
        create_elem_mock.assert_not_called()

        # 2) temp cube creation
        cube_cls.assert_called_once_with(
            name=temp_cube_name,
            dimensions=dims_new,
            rules="",
        )
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp with target_dim_mapping
        assert copy_mock.call_count == 2
        first_call = copy_mock.call_args_list[0]
        first_kwargs = first_call.kwargs

        assert first_kwargs["tm1_service"] is tm1_service
        assert first_kwargs["target_cube_name"] == temp_cube_name
        assert first_kwargs["target_dim_mapping"] == {"Region": "Leaf1"}
        assert first_kwargs["clear_target"] is True
        mdx1 = first_kwargs["data_mdx"]
        assert "[Sales]" in mdx1
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "Region" not in mdx1

        # 3) original cube deleted, cube recreated with new definition
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final (no target_dim_mapping)
        second_call = copy_mock.call_args_list[1]
        second_kwargs = second_call.kwargs

        assert second_kwargs["tm1_service"] is tm1_service
        assert second_kwargs["target_cube_name"] == "Sales"
        assert second_kwargs["clear_target"] is True
        assert "target_dim_mapping" not in second_kwargs
        mdx2 = second_kwargs["data_mdx"]
        assert "[Sales__tmp_add_dims]" in mdx2
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "TM1SUBSETALL([Region])" in mdx2

        # 5) temp cube deletion
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip(reason="Ignored per user request")
    def test_add_dimension_to_cube_creates_default_leaf_when_no_leaf_exists(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version"]
        dims_new = ["Version", "NewDim"]

        # Hierarchy with only consolidated elements (no leaves)
        hier = mocker.Mock()
        cons = mocker.Mock()
        cons.name = "Total"
        cons.element_type = "Consolidated"
        hier.elements.values.return_value = [cons]
        tm1_service.hierarchies.get.return_value = hier

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) dimension created, then hierarchy default element created
        create_dimension_mock.assert_called_once_with(
            tm1_service=tm1_service,
            dimension="NewDim",
        )

        tm1_service.hierarchies.get.assert_called_with(
            dimension_name="NewDim",
            hierarchy_name="NewDim",
        )

        create_elem_mock.assert_called_once()
        elem_kwargs = create_elem_mock.call_args.kwargs
        elem_attributes = elem_kwargs["element"].body_as_dict
        assert elem_kwargs["dimension_name"] == "NewDim"
        assert elem_kwargs["hierarchy_name"] == "NewDim"
        assert elem_kwargs["element"].name == "Legacy Data"
        assert elem_attributes["Type"] == "Numeric"

        # hierarchy should be updated with the new element
        hier.add_element.assert_called_once_with(
            element_name="Legacy Data",
            element_type="Numeric",
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy=hier)

        # 2) temp cube created with new dimensions
        assert cube_cls.call_count == 2

        # First call should be for the temp cube, using keyword args
        temp_call = cube_cls.call_args_list[0]
        assert temp_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }

        # 3) first copy uses 'Legacy Data' as target_dim_mapping
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_dim_mapping"] == {"NewDim": "Legacy Data"}


    @pytest.mark.skip
    def test_add_dimension_to_cube_raises_on_cube_name_mismatch(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales_Old")
        cube_new = make_cube("Sales_New")

        with pytest.raises(ValueError) as excinfo:
            cube._add_dimensions_to_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=["Version"],
                dims_new=["Version", "Region"],
            )

        assert "Cube name mismatch" in str(excinfo.value)

        tm1_service.cubes.create.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_sum_all_default_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year", "Region"]
        dims_new = ["Version", "Year"]

        # TM1: temp cube does not exist yet
        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=None,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # 1) temp cube created with reduced dims
        assert cube_cls.call_count >= 1
        first_cube_call = cube_cls.call_args_list[0]
        assert first_cube_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp
        assert copy_mock.call_count == 2
        first_call_kwargs = copy_mock.call_args_list[0].kwargs

        assert first_call_kwargs["tm1_service"] is tm1_service
        assert first_call_kwargs["target_cube_name"] == temp_cube_name
        # sum_all => no explicit source_dim_mapping
        assert first_call_kwargs.get("source_dim_mapping") is None
        assert first_call_kwargs["clear_target"] is True
        assert first_call_kwargs["sum_numeric_duplicates"] is True

        mdx1 = first_call_kwargs["data_mdx"]
        # All deleted dims use TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert "FILTER(" not in mdx1  # no keep_by_attr filters here

        # 3) original cube deleted & recreated
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final
        second_call_kwargs = copy_mock.call_args_list[1].kwargs
        assert second_call_kwargs["target_cube_name"] == "Sales"
        assert second_call_kwargs["clear_target"] is True
        assert second_call_kwargs["sum_numeric_duplicates"] is True
        mdx2 = second_call_kwargs["data_mdx"]
        # Now only new dims appear
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "Region" not in mdx2

        # 5) temp cube deleted at the end
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Year"]

        strategies = {
            "Version": {
                "strategy": "keep_element",
                "element": "Actual",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # temp cube created as before
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        # first bedrock call: old -> temp
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_element => using source_dim_mapping for Version
        assert first_kwargs["source_dim_mapping"] == {"Version": "Actual"}
        assert first_kwargs["sum_numeric_duplicates"] is True

        # MDX still uses TM1SUBSETALL for Version; filtering is handled by source_dim_mapping
        mdx1 = first_kwargs["data_mdx"]
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "FILTER(" not in mdx1

        # clean-up flow same as sum_all
        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_requires_element(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Version"]
        dims_new = []

        strategies = {
            "Version": {
                "strategy": "keep_element",
                # 'element' missing on purpose
            }
        }

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires an 'element' key" in str(excinfo.value)
        # Must not call bedrock if config is invalid
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Region", "Year"]
        dims_new = ["Version", "Year"]

        strategies = {
            "Region": {
                "strategy": "keep_by_attr",
                "attr_name": "KeepOnDrop",
                "attr_value": "Y",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_by_attr => no source_dim_mapping
        assert first_kwargs.get("source_dim_mapping") is None

        mdx1 = first_kwargs["data_mdx"]
        # Version & Year are standard TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1

        # Region uses FILTER with attribute logic
        assert "FILTER(" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert '[Region].CURRENTMEMBER.PROPERTIES("KeepOnDrop")' in mdx1
        assert '= "Y"' in mdx1

        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.parametrize("bad_cfg", [
        {"strategy": "keep_by_attr", "attr_name": "KeepOnDrop"},  # no attr_value
        {"strategy": "keep_by_attr", "attr_value": "Y"},  # no attr_name
        {"strategy": "keep_by_attr"},  # both missing
    ])
    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_requires_attr_name_and_value(self, mocker, bad_cfg):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Region"]
        dims_new = []  # delete Region

        strategies = {"Region": bad_cfg}

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires 'attr_name' and 'attr_value'" in str(excinfo.value)
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_no_deleted_dims_returns_early(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year"]

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        cube_cls.assert_not_called()
        copy_mock.assert_not_called()
        delete_cube_mock.assert_not_called()
        create_cube_mock.assert_not_called()
        tm1_service.cubes.exists.assert_not_called()

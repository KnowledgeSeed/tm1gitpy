from tests.unit_common import *


class TestHierarchyCRUD:

    def test_create_hierarchy_does_not_create_edges_or_elements(self, mocker):
        tm1_service = mocker.Mock()

        elements = [make_element("E1"), make_element("E2")]
        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_A",
            hierarchy_name="Hierarchy_A",
            elements=elements,
            edges=[
                Edge(parent="Total", component_name="E1", weight=1),
                Edge(parent="Total", component_name="E2", weight=2),
            ],
        )

        tm1py_hierarchy_cls = mocker.patch("tm1_git_py.model.hierarchy.TM1py.Hierarchy")
        tm1py_hierarchy_obj = tm1py_hierarchy_cls.return_value

        response = mocker.Mock()
        tm1_service.hierarchies.create.return_value = response
        create_element_mock = mocker.patch("tm1_git_py.model.hierarchy.create_element")

        result = hierarchy.create_hierarchy(
            tm1_service,
            hierarchy_mock,
            uri=Hierarchy.uri_for("Dimension_A", "Hierarchy_A"),
        )

        # Assert: TM1py.Hierarchy constructed with correct name + dimension
        tm1py_hierarchy_cls.assert_called_once_with(
            name="Hierarchy_A",
            dimension_name="Dimension_A",
        )

        # TM1 service called to create hierarchy
        tm1_service.hierarchies.create.assert_called_once_with(tm1py_hierarchy_obj)
        assert result is response

        # create_hierarchy only creates the hierarchy itself now.
        tm1py_hierarchy_obj.add_edge.assert_not_called()
        tm1_service.elements.exists.assert_not_called()
        create_element_mock.assert_not_called()


    def test_delete_hierarchy_calls_tm1_with_correct_dimension_and_name(self, mocker):
        tm1_service = mocker.Mock()

        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )

        tm1_service.hierarchies.delete.return_value = "delete-result"

        result = hierarchy.delete_hierarchy(
            tm1_service,
            hierarchy_mock,
            uri=Hierarchy.uri_for("Dimension_X", "Hierarchy_Delete"),
        )

        tm1_service.hierarchies.delete.assert_called_once_with(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )
        assert result == "delete-result"

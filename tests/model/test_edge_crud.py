from tests.unit_common import *


class TestEdgeCRUD:

    def test_create_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_A",
            component_name="Child_A",
            weight=1,
        )
        tm1_service.elements.add_edges.return_value = "create-result"

        result = edge.create_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_A", "Hier_A", "Parent_A", "Child_A"),
        )

        tm1_service.elements.add_edges.assert_called_once_with(
            "Hier_A",
            "Dim_A",
            {("Parent_A", "Child_A"): 1},
        )
        assert result == "create-result"


    def test_update_edge_fetches_hierarchy_and_updates_edge(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_B",
            component_name="Child_B",
            weight=2,
        )

        hierarchy_object = mocker.Mock()
        tm1_service.hierarchies.get.return_value = hierarchy_object
        tm1_service.hierarchies.update.return_value = "update-result"

        result = edge.update_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_B", "Hier_B", "Parent_B", "Child_B"),
        )

        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Dim_B",
            hierarchy_name="Hier_B",
        )
        hierarchy_object.update_edge.assert_called_once_with(
            parent="Parent_B",
            component="Child_B",
            weight=2,
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy_object)
        assert result == "update-result"


    def test_delete_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_C",
            component_name="Child_C",
            weight=3,
        )
        tm1_service.elements.remove_edge.return_value = "delete-result"

        result = edge.delete_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_C", "Hier_C", "Parent_C", "Child_C"),
        )

        tm1_service.elements.remove_edge.assert_called_once_with(
            "Hier_C",
            "Dim_C",
            "Parent_C",
            "Child_C",
        )
        assert result == "delete-result"

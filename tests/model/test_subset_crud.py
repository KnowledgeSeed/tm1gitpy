from tests.unit_common import *


class TestSubsetCRUD:

    def test_create_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].Members}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1py_subset_cls = mocker.patch("tm1_git_py.model.subset.TM1py.Subset")
        tm1py_subset_instance = tm1py_subset_cls.return_value
        tm1_service.subsets.create.return_value = "create-result"

        result = subset.create_subset(
            tm1_service,
            subset_mock,
            uri=Subset.uri_for("Dim_A", "Hier_A", "Subset_A"),
        )

        tm1py_subset_cls.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
            expression="{[Dim_A].[Hier_A].Members}",
        )
        tm1_service.subsets.create.assert_called_once_with(tm1py_subset_instance)
        assert result == "create-result"

    def test_delete_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_Delete",
            expression="{[Dim_Del].[Hier_Del].Members}",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )

        tm1_service.subsets.delete.return_value = "delete-result"

        result = subset.delete_subset(
            tm1_service,
            subset_mock,
            uri=Subset.uri_for("Dim_Del", "Hier_Del", "Subset_Delete"),
        )

        tm1_service.subsets.delete.assert_called_once_with(
            subset_name="Subset_Delete",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )
        assert result == "delete-result"


    def test_update_subset_updates_expression_and_calls_tm1(self, mocker):
        tm1_service = mocker.Mock()

        subset_new = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].NewMembers}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1_subset_obj = mocker.Mock()
        tm1_subset_obj.expression = "{[Dim_A].[Hier_A].OldMembers}"
        tm1_service.subsets.get.return_value = tm1_subset_obj

        tm1_service.subsets.update.return_value = "update-result"

        result = subset.update_subset(
            tm1_service,
            subset_new,
            uri=Subset.uri_for("Dim_A", "Hier_A", "Subset_A"),
        )

        tm1_service.subsets.get.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        assert tm1_subset_obj.expression == "{[Dim_A].[Hier_A].NewMembers}"
        tm1_service.subsets.update.assert_called_once_with(tm1_subset_obj)
        assert result == "update-result"

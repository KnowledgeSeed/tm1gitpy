from tests.unit_common import *


class TestDimensionCRUD:

    def test_create_dimension_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        dimension_input = mocker.Mock()
        dimension_input.name = "TestDim"

        tm1py_dimension_cls = mocker.patch("tm1_git_py.model.dimension.TM1py.Dimension")
        tm1py_dimension_instance = tm1py_dimension_cls.return_value
        tm1_service.dimensions.create.return_value = "create-result"

        result = dimension.create_dimension(tm1_service, dimension_input.name)

        tm1py_dimension_cls.assert_called_once_with("TestDim")
        tm1_service.dimensions.create.assert_called_once_with(tm1py_dimension_instance)
        assert result == "create-result"


    def test_delete_dimension_calls_delete_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.dimensions.delete.return_value = "delete-result"
        dim = make_dimension(name="TestDim", source_path="dimensions/TestDim.json")

        result = dimension.delete_dimension(tm1_service, dim)

        tm1_service.dimensions.delete.assert_called_once_with("TestDim")
        assert result == "delete-result"

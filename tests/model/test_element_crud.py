from tests.unit_common import *


class TestElementCRUD:

    def test_create_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_A",
            type="Numeric",
        )

        tm1py_element_cls = mocker.patch("tm1_git_py.model.element.TM1py.Element")
        tm1py_element_instance = tm1py_element_cls.return_value
        tm1_service.elements.create.return_value = "create-result"

        result = element.create_element(
            tm1_service,
            element_obj,
            uri=Element.uri_for("Dim_A", "Hier_A", "Elem_A"),
        )

        tm1py_element_cls.assert_called_once_with(name="Elem_A", element_type="Numeric")
        tm1_service.elements.create.assert_called_once_with(
            hierarchy_name="Hier_A",
            dimension_name="Dim_A",
            element=tm1py_element_instance,
        )
        assert result == "create-result"


    def test_delete_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_B",
            type="String",
        )
        tm1_service.elements.delete.return_value = "delete-result"

        result = element.delete_element(
            tm1_service,
            element_obj,
            uri=Element.uri_for("Dim_B", "Hier_B", "Elem_B"),
        )

        tm1_service.elements.delete.assert_called_once_with(
            hierarchy_name="Hier_B",
            dimension_name="Dim_B",
            element_name="Elem_B",
        )
        assert result == "delete-result"

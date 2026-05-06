from tests.unit_common import *


class TestProcessCRUD:

    def test_create_process_builds_tm1py_process_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        process_mock = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
        )

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )

        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"


    def test_delete_process_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.delete.return_value = "delete-result"
        proc = make_process(name="Proc_To_Delete")

        result = process.delete_process(tm1_service, proc)

        tm1_service.processes.delete.assert_called_once_with("Proc_To_Delete")
        assert result == "delete-result"


    def test_update_process_updates_core_fields_without_param_var_changes(self, mocker):
        tm1_service = mocker.Mock()

        process_new = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="ODBC"
        )

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections coming from the live TM1 object.
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        # Act
        result = process.update_process(tm1_service, process_new)

        tm1_service.processes.get.assert_called_once_with(name_process="Proc_A")

        # Core fields updated
        assert tm1_process_obj.datasource_type == "ODBC"
        assert tm1_process_obj.has_security_access is True

        # No parameter/variable modifications because lists are identical
        tm1_process_obj.add_parameter.assert_not_called()
        tm1_process_obj.remove_parameter.assert_not_called()
        tm1_process_obj.add_variable.assert_not_called()
        tm1_process_obj.remove_variable.assert_not_called()

        # Update call + propagated result
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"


    def test_update_process_adds_and_removes_parameters_and_variables(self, mocker):
        tm1_service = mocker.Mock()

        params_new = [
            {"Name": "p1", "Prompt": "P1", "Value": "1", "Type": "Numeric"},
            {"Name": "p3", "Prompt": "P3", "Value": "3", "Type": "String"},
        ]
        vars_new = [
            {"Name": "v1", "Type": "String"},
            {"Name": "v3", "Type": "String"},
        ]

        process_new = make_process(
            name="Proc_B",
            has_security_access=False,
            datasource_type="None",
            parameters=params_new,
            variables=vars_new,
        )

        params_old = [
            {"Name": "p1", "Prompt": "P1", "Value": "1", "Type": "Numeric"},
            {"Name": "p2", "Prompt": "P2", "Value": "2", "Type": "String"},
        ]
        vars_old = [
            {"Name": "v1", "Type": "String"},
            {"Name": "v2", "Type": "String"},
        ]

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections fetched from the live TM1 process.
        tm1_process_obj.parameters = list(params_old)
        tm1_process_obj.variables = list(vars_old)
        tm1_service.processes.get.return_value = tm1_process_obj

        update_result = mocker.sentinel.update_result
        tm1_service.processes.update.return_value = update_result

        # Act
        result = process.update_process(tm1_service, process_new)

        # Check add/remove for parameters
        tm1_process_obj.add_parameter.assert_called_once_with(
            name="p3",
            prompt="P3",
            value="3",
            parameter_type="String",
        )
        tm1_process_obj.remove_parameter.assert_called_once_with(name="p2")

        # Check add/remove for variables
        tm1_process_obj.add_variable.assert_called_once_with(
            name="v3",
            variable_type="String",
        )
        tm1_process_obj.remove_variable.assert_called_once_with(name="v2")

        # Ensure update was still called with the process object
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result is update_result

    def test_create_process_accepts_datasource_dict(self, mocker):
        tm1_service = mocker.Mock()
        process_mock = make_process(name="Proc_DictDS", datasource_type={"type": "None"})

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_DictDS",
            has_security_access=process_mock.hasSecurityAccess,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"

    def test_update_process_normalizes_empty_datasource_to_none(self, mocker):
        tm1_service = mocker.Mock()
        process_new = make_process(name="Proc_EmptyDS", datasource_type="")

        tm1_process_obj = mocker.Mock()
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        result = process.update_process(tm1_service, process_new)

        assert tm1_process_obj.datasource_type == "None"
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"

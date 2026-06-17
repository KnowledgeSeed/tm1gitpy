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

    def test_update_process_sets_variables_ui_data_after_variable_removal(self, mocker):
        tm1_service = mocker.Mock()
        process_new = make_process(
            name="Proc_UI_Remove",
            datasource_type="None",
            variables=[
                {"Name": "v1", "Type": "String", "Position": 1},
                {"Name": "v2", "Type": "String", "Position": 2},
            ],
        )
        process_new.variables_ui_data = ["target-v1", "target-v2"]

        class FakeTM1Process:
            def __init__(self):
                self.parameters = list(process_new.parameters)
                self.variables = [
                    {"Name": "v1", "Type": "String", "Position": 1},
                    {"Name": "v2", "Type": "String", "Position": 2},
                    {"Name": "vTmp", "Type": "String", "Position": 3},
                ]
                self._variables_ui_data = ["live-v1", "live-v2", "live-vTmp"]

            def add_variable(self, name, variable_type):
                self.variables.append({"Name": name, "Type": variable_type})
                self._variables_ui_data.append(f"live-{name}")

            def remove_variable(self, name):
                for variable in self.variables[:]:
                    if variable["Name"] == name:
                        index = self.variables.index(variable)
                        self._variables_ui_data.pop(index)
                        self.variables.remove(variable)

        tm1_process_obj = FakeTM1Process()
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        result = process.update_process(tm1_service, process_new)

        assert result == "update-result"
        assert tm1_process_obj.variables == process_new.variables
        assert tm1_process_obj._variables_ui_data == ["target-v1", "target-v2"]

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

    def test_create_process_maps_datasource_dict_to_tm1py_constructor_kwargs(self, mocker):
        tm1_service = mocker.Mock()
        process_mock = make_process(
            name="Proc_ASCII_DS",
            datasource_type={
                "Type": "ASCII",
                "asciiDecimalSeparator": ",",
                "asciiDelimiterChar": ";",
                "asciiDelimiterType": "Character",
                "asciiHeaderRecords": 2,
                "asciiQuoteCharacter": "\"",
                "asciiThousandSeparator": ".",
                "dataSourceNameForClient": "client.csv",
                "dataSourceNameForServer": "server.csv",
                "userName": "svc_user",
                "password": "secret",
                "query": "SELECT 1",
                "usesUnicode": False,
            },
        )

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_ASCII_DS",
            has_security_access=process_mock.hasSecurityAccess,
            datasource_type="ASCII",
            datasource_ascii_decimal_separator=",",
            datasource_ascii_delimiter_char=";",
            datasource_ascii_delimiter_type="Character",
            datasource_ascii_header_records=2,
            datasource_ascii_quote_character="\"",
            datasource_ascii_thousand_separator=".",
            datasource_data_source_name_for_client="client.csv",
            datasource_data_source_name_for_server="server.csv",
            datasource_user_name="svc_user",
            datasource_password="secret",
            datasource_query="SELECT 1",
            datasource_uses_unicode=False,
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )
        assert result == "create-result"

    def test_create_process_passes_ui_data_and_variables_ui_data_when_present(self, mocker):
        tm1_service = mocker.Mock()
        process_mock = make_process(name="Proc_UI")
        process_mock.ui_data = "CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f"
        process_mock.variables_ui_data = [
            "VarType=32\fColType=827\f",
            "VarType=33\fColType=827\f",
        ]

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_UI",
            has_security_access=process_mock.hasSecurityAccess,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
            ui_data="CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f",
            variables_ui_data=[
                "VarType=32\fColType=827\f",
                "VarType=33\fColType=827\f",
            ],
        )
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        tm1_service.processes.update.assert_called_once_with(tm1py_process_instance)
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

    def test_process_as_json_includes_ui_data_and_variables_ui_data_when_present(self):
        proc = Process(
            name="Proc_UI",
            hasSecurityAccess=False,
            code_link="Proc_UI.ti",
            datasource="None",
            parameters=[],
            variables=[],
            ti=TI("", "", "", ""),
            ui_data="CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f",
            variables_ui_data=["VarType=32\fColType=827\f"],
        )

        payload = json.loads(proc.as_json())
        assert payload["UIData"] == "CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f"
        assert payload["VariablesUIData"] == ["VarType=32\fColType=827\f"]
        assert list(payload.keys()) == [
            "@type",
            "Name",
            "HasSecurityAccess",
            "Code@Code.link",
            "UIData",
            "VariablesUIData",
            "DataSource",
            "Parameters",
            "Variables",
        ]

    def test_process_as_json_omits_ui_data_and_variables_ui_data_when_empty_or_none(self):
        proc_empty = Process(
            name="Proc_Empty",
            hasSecurityAccess=False,
            code_link="Proc_Empty.ti",
            datasource="None",
            parameters=[],
            variables=[],
            ti=TI("", "", "", ""),
            ui_data="   ",
            variables_ui_data=[],
        )
        proc_none = Process(
            name="Proc_None",
            hasSecurityAccess=False,
            code_link="Proc_None.ti",
            datasource="None",
            parameters=[],
            variables=[],
            ti=TI("", "", "", ""),
            ui_data=None,
            variables_ui_data=None,
        )

        assert "UIData" not in json.loads(proc_empty.as_json())
        assert "UIData" not in json.loads(proc_none.as_json())
        assert "VariablesUIData" not in json.loads(proc_empty.as_json())
        assert "VariablesUIData" not in json.loads(proc_none.as_json())

    def test_process_from_dict_and_to_dict_support_ui_data_and_variables_ui_data(self):
        source = {
            "name": "Proc_Dict",
            "has_security_access": True,
            "code_link": "Proc_Dict.ti",
            "datasource": "None",
            "parameters": [],
            "variables": [],
            "ui_data": "CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f",
            "variables_ui_data": ["VarType=32\fColType=827\f"],
            "ti": {
                "prolog_procedure": "",
                "metadata_procedure": "",
                "data_procedure": "",
                "epilog_procedure": "",
            },
        }

        proc = Process.from_dict(source)
        assert proc.ui_data == "CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f"
        assert proc.variables_ui_data == ["VarType=32\fColType=827\f"]
        assert proc.to_dict()["ui_data"] == "CubeAction=1511\fDataAction=1503\fCubeLogChanges=0\f"
        assert proc.to_dict()["variables_ui_data"] == ["VarType=32\fColType=827\f"]

    def test_process_equality_and_hash_include_ui_data_and_variables_ui_data(self):
        base_kwargs = {
            "name": "Proc_EQ",
            "hasSecurityAccess": True,
            "code_link": "Proc_EQ.ti",
            "datasource": "None",
            "parameters": [],
            "variables": [],
            "ti": TI("a", "b", "c", "d"),
        }
        proc_a = Process(**base_kwargs, ui_data="X", variables_ui_data=["A"])
        proc_b = Process(**base_kwargs, ui_data="X", variables_ui_data=["A"])
        proc_c = Process(**base_kwargs, ui_data="Y", variables_ui_data=["A"])
        proc_d = Process(**base_kwargs, ui_data="X", variables_ui_data=["B"])

        assert proc_a == proc_b
        assert hash(proc_a) == hash(proc_b)
        assert proc_a != proc_c
        assert proc_a != proc_d

    def test_process_as_json_preserves_full_datasource_dict_and_omits_password(self):
        datasource = {
            "Type": "ODBC",
            "dataSourceNameForClient": "CLIENT_DSN",
            "dataSourceNameForServer": "SERVER_DSN",
            "userName": "svc_user",
            "password": "secret",
            "query": "SELECT 1",
            "usesUnicode": True,
        }
        proc = Process(
            name="Proc_DS",
            hasSecurityAccess=False,
            code_link="Proc_DS.ti",
            datasource=datasource,
            parameters=[],
            variables=[],
            ti=TI("", "", "", ""),
        )

        payload = json.loads(proc.as_json())
        assert payload["DataSource"]["Type"] == "ODBC"
        assert payload["DataSource"]["dataSourceNameForClient"] == "CLIENT_DSN"
        assert payload["DataSource"]["dataSourceNameForServer"] == "SERVER_DSN"
        assert payload["DataSource"]["userName"] == "svc_user"
        assert payload["DataSource"]["query"] == "SELECT 1"
        assert payload["DataSource"]["usesUnicode"] is True
        assert "password" not in payload["DataSource"]

    def test_process_as_json_datasource_string_falls_back_to_type_object(self):
        proc = Process(
            name="Proc_Legacy",
            hasSecurityAccess=False,
            code_link="Proc_Legacy.ti",
            datasource="ASCII",
            parameters=[],
            variables=[],
            ti=TI("", "", "", ""),
        )

        payload = json.loads(proc.as_json())
        assert payload["DataSource"] == {"Type": "ASCII"}

    def test_process_from_dict_preserves_datasource_dict(self):
        source = {
            "Name": "Proc_DictDS",
            "HasSecurityAccess": False,
            "Code@Code.link": "Proc_DictDS.ti",
            "DataSource": {
                "Type": "ASCII",
                "asciiDelimiterChar": ";",
                "password": "secret",
            },
            "Parameters": [],
            "Variables": [],
            "ti": {
                "prolog_procedure": "",
                "metadata_procedure": "",
                "data_procedure": "",
                "epilog_procedure": "",
            },
        }

        proc = Process.from_dict(source)
        assert isinstance(proc.datasource, dict)
        assert proc.datasource["Type"] == "ASCII"
        assert proc.datasource["asciiDelimiterChar"] == ";"
        assert proc.to_dict()["datasource"]["Type"] == "ASCII"
        assert "password" not in proc.to_dict()["datasource"]

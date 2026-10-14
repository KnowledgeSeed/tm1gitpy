import json
from pathlib import Path

import pytest

from TM1py.Exceptions import TM1pyRestException

import tm1_git_py.services.apply as apply_module
from tm1_git_py.services.apply import (
    _apply_declared_parameter_types,
    _build_execute_process_statement,
    _build_unified_deployment_hook_process,
    _create_and_run_temp_process,
    _load_tm1project_json,
    _merge_parameter_overrides,
    _resolve_declared_parameters,
    _ti_numeric_literal,
    _parse_tasks_from_project_file,
    _resolve_operation_process_specs,
    apply_post_pull_operations,
    apply_post_push_operations,
    _apply_deployment_hook_operations,
    apply_pre_pull_operations,
    apply_pre_push_operations,
    get_pre_pull_operations,
)


def _tm1_service_with_processes(mocker, declared=None):
    """Mock TM1 service whose processes declare the given parameters.

    ``declared`` maps a process name to ``[(name, type), ...]``; a process that
    is not listed declares no parameters.
    """
    declared = declared or {}
    tm1_service = mocker.Mock()

    def get(process_name):
        return mocker.Mock(
            parameters=[
                {"Name": name, "Prompt": "", "Value": "", "Type": param_type}
                for name, param_type in declared.get(process_name, [])
            ]
        )

    tm1_service.processes.get.side_effect = get
    tm1_service.server.get_message_log_entries.return_value = []
    return tm1_service


PRUN_PROJECT = {
    "Tasks": {
        "DropRules": {
            "Process": "Processes('Clear Rules')",
            "Parameters": [{"Name": "pRun", "Value": "0"}],
        },
        "CleanCubes": {"Process": "Processes('Clear Data')"},
    },
    "Deployment": {
        "prod": {"PrePull": ["Tasks('DropRules')", "Tasks('CleanCubes')"]}
    },
}
PRUN_DECLARED = {
    "Clear Rules": [("pRun", "Numeric"), ("pMode", "String")],
    "Clear Data": [("pRun", "Numeric")],
}


class TestApplyDeploymentHookOperations:
    def test_builds_unified_process_from_tm1project_and_executes_it(
        self, mocker, tmp_path
    ):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(
            json.dumps(
                {
                    "Version": "1.0",
                    "Tasks": {
                        "Backup": {
                            "Process": "Processes('zSYS Backup')",
                            "Parameters": [
                                {"Name": "pWait", "Value": "1"},
                            ],
                        },
                        "PrePullDropRules": {
                            "Process": "Processes('zSYS Maintenance Clear All Cube Rule')",
                        },
                    },
                    "Deployment": {
                        "dev": {
                            "PrePull": [
                                "Tasks('Backup')",
                                "Tasks('PrePullDropRules')",
                            ]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        tm1_service = _tm1_service_with_processes(
            mocker, {"zSYS Backup": [("pWait", "Numeric")]}
        )
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "created"
        tm1_service.processes.execute.return_value = "executed"
        tm1_service.processes.delete.return_value = "deleted"

        result = apply_pre_pull_operations(
            tm1_service=tm1_service,
            project_file_path=project_path,
            environment="dev",
            timeout=30,
        )

        tm1py_process_cls.assert_called_once()
        process_kwargs = tm1py_process_cls.call_args.kwargs
        assert process_kwargs["name"].startswith("tm1_git_py_prepull_dev_")
        assert process_kwargs["has_security_access"] is False
        assert process_kwargs["datasource_type"] == "None"
        assert process_kwargs["metadata_procedure"] == ""
        assert process_kwargs["data_procedure"] == ""
        assert process_kwargs["epilog_procedure"] == ""
        assert process_kwargs["prolog_procedure"] == (
            "ExecuteProcess('zSYS Backup', 'pWait', 1);\n"
            "ExecuteProcess('zSYS Maintenance Clear All Cube Rule');"
        )
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        tm1_service.processes.execute.assert_called_once_with(
            process_name=process_kwargs["name"],
            parameters={},
            timeout=30,
        )
        tm1_service.processes.delete.assert_called_once_with(process_kwargs["name"])
        assert result == "executed"

    def test_returns_none_when_no_prepull_tasks(self, mocker, tmp_path):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(
            json.dumps(
                {
                    "Version": "1.0",
                    "Tasks": {},
                    "Deployment": {
                        "dev": {
                            "PrePull": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        tm1_service = mocker.Mock()
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")

        result = apply_pre_pull_operations(
            tm1_service=tm1_service,
            project_file_path=project_path,
            environment="dev",
        )

        tm1py_process_cls.assert_not_called()
        tm1_service.processes.create.assert_not_called()
        tm1_service.processes.execute.assert_not_called()
        tm1_service.processes.delete.assert_not_called()
        assert result is None

    @pytest.mark.parametrize(
        "apply_fn,operation,prefix",
        [
            (apply_post_pull_operations, "PostPull", "tm1_git_py_postpull_dev_"),
            (apply_pre_push_operations, "PrePush", "tm1_git_py_prepush_dev_"),
            (apply_post_push_operations, "PostPush", "tm1_git_py_postpush_dev_"),
        ],
    )
    def test_operation_wrappers_route_to_temp_process(
        self, mocker, tmp_path, apply_fn, operation, prefix
    ):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(
            json.dumps(
                {
                    "Version": "1.0",
                    "Tasks": {"TaskA": {"Process": "Processes('ProcA')"}},
                    "Deployment": {"dev": {operation: ["Tasks('TaskA')"]}},
                }
            ),
            encoding="utf-8",
        )

        tm1_service = _tm1_service_with_processes(mocker)
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.execute.return_value = "executed"

        result = apply_fn(
            tm1_service=tm1_service,
            project_file_path=project_path,
            environment="dev",
        )

        assert tm1py_process_cls.call_args.kwargs["name"].startswith(prefix)
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        tm1_service.processes.execute.assert_called_once()
        tm1_service.processes.delete.assert_called_once()
        assert result == "executed"

    @pytest.mark.parametrize(
        "wrapper,operation",
        [
            (apply_pre_pull_operations, "PrePull"),
            (apply_post_pull_operations, "PostPull"),
            (apply_pre_push_operations, "PrePush"),
            (apply_post_push_operations, "PostPush"),
        ],
    )
    def test_operation_wrappers_delegate_to_apply_hook(self, wrapper, operation):
        assert wrapper.func is _apply_deployment_hook_operations
        assert wrapper.keywords == {"operation": operation}


class TestApplyDeploymentHookHelpers:
    def test_load_tm1project_json_returns_object(self, tmp_path):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(json.dumps({"Version": "1.0"}), encoding="utf-8")

        loaded_path, project_file = _load_tm1project_json(project_path)

        assert loaded_path == project_path
        assert project_file == {"Version": "1.0"}

    def test_load_tm1project_json_rejects_non_object_json(self, tmp_path):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(json.dumps([]), encoding="utf-8")

        with pytest.raises(
            ValueError, match="tm1project file must contain a JSON object"
        ):
            _load_tm1project_json(project_path)

    def test_parse_tasks_from_project_file_happy_path(self):
        specs = _parse_tasks_from_project_file(
            task_refs=["Tasks('Backup')", "Tasks('Cleanup')"],
            tasks={
                "Backup": {
                    "Process": "Processes('Proc Backup')",
                    "Parameters": [
                        {"Name": "pWait", "Value": "1"},
                        {"name": "pMode", "value": "fast"},
                    ],
                },
                "Cleanup": {
                    "Process": "Processes('Proc Cleanup')",
                },
            },
            operation="PrePull",
        )

        assert specs == [
            {
                "task_name": "Backup",
                "process_name": "Proc Backup",
                "parameters": [
                    {"Name": "pWait", "Value": "1"},
                    {"name": "pMode", "value": "fast"},
                ],
            },
            {
                "task_name": "Cleanup",
                "process_name": "Proc Cleanup",
                "parameters": [],
            },
        ]

    @pytest.mark.parametrize(
        "task_refs,tasks,match",
        [
            ([None], {}, "Invalid PrePull task reference"),
            (["Tasks('Missing')"], {}, "Task 'Missing' not found in project Tasks"),
            (
                ["Tasks('NoProcess')"],
                {"NoProcess": {}},
                "Task 'NoProcess' missing Process reference",
            ),
        ],
    )
    def test_parse_tasks_from_project_file_rejects_bad_input(
        self, task_refs, tasks, match
    ):
        with pytest.raises(ValueError, match=match):
            _parse_tasks_from_project_file(
                task_refs=task_refs,
                tasks=tasks,
                operation="PrePull",
            )

    @pytest.mark.parametrize(
        "operation",
        ["PrePull", "PostPull", "PrePush", "PostPush"],
    )
    def test_resolve_operation_process_specs_returns_empty_when_no_task_refs(
        self, tmp_path, operation
    ):
        project_file = {
            "Deployment": {"dev": {operation: []}},
            "Tasks": {"TaskA": {"Process": "Processes('ProcA')"}},
        }

        result = _resolve_operation_process_specs(
            project_json=project_file,
            environment="dev",
            operation=operation,
            project_path=tmp_path / "tm1project.json",
        )

        assert result == []

    def test_resolve_operation_process_specs_rejects_non_dict_tasks(self, tmp_path):
        project_file = {
            "Deployment": [1],
            "Tasks": [],
        }

        with pytest.raises(
            ValueError, match="Deployment must be a JSON object when present"
        ):
            _resolve_operation_process_specs(
                project_json=project_file,
                environment="dev",
                operation="PrePull",
                project_path=tmp_path / "tm1project.json",
            )

    def test_build_unified_deployment_hook_process_builds_expected_process(
        self, mocker
    ):
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")

        _build_unified_deployment_hook_process(
            process_specs=[
                {
                    "process_name": "ProcA",
                    "parameters": [
                        ("pWait", "1", "Numeric"),
                        ("pMode", "it's", "String"),
                    ],
                },
                {"process_name": "ProcB", "parameters": []},
            ],
            process_name="temp_process",
        )

        tm1py_process_cls.assert_called_once_with(
            name="temp_process",
            has_security_access=False,
            prolog_procedure=(
                "ExecuteProcess('ProcA', 'pWait', 1, 'pMode', 'it''s');\n"
                "ExecuteProcess('ProcB');"
            ),
            metadata_procedure="",
            data_procedure="",
            epilog_procedure="",
            datasource_type="None",
        )

    def test_create_and_run_temp_process_cleans_up_on_success(self, mocker):
        tm1_service = _tm1_service_with_processes(mocker)
        tm1_service.processes.execute.return_value = "executed"
        build_mock = mocker.patch(
            "tm1_git_py.services.apply._build_unified_deployment_hook_process"
        )
        uuid_mock = mocker.patch("tm1_git_py.services.apply.uuid.uuid4")
        uuid_mock.return_value.hex = "abc123"

        result = _create_and_run_temp_process(
            tm1_service=tm1_service,
            process_specs=[{"process_name": "ProcA", "parameters": []}],
            environment="dev",
            operation="PrePull",
            timeout=30,
        )

        build_mock.assert_called_once_with(
            [{"process_name": "ProcA", "parameters": []}],
            "tm1_git_py_prepull_dev_abc123",
        )
        tm1_service.processes.create.assert_called_once_with(build_mock.return_value)
        tm1_service.processes.execute.assert_called_once_with(
            process_name="tm1_git_py_prepull_dev_abc123",
            parameters={},
            timeout=30,
        )
        tm1_service.processes.delete.assert_called_once_with(
            "tm1_git_py_prepull_dev_abc123"
        )
        assert result == "executed"

    def test_create_and_run_temp_process_cleans_up_on_execute_error(self, mocker):
        tm1_service = _tm1_service_with_processes(mocker)
        tm1_service.processes.execute.side_effect = RuntimeError("boom")
        build_mock = mocker.patch(
            "tm1_git_py.services.apply._build_unified_deployment_hook_process"
        )
        uuid_mock = mocker.patch("tm1_git_py.services.apply.uuid.uuid4")
        uuid_mock.return_value.hex = "abc123"

        with pytest.raises(RuntimeError, match="boom"):
            _create_and_run_temp_process(
                tm1_service=tm1_service,
                process_specs=[{"process_name": "ProcA", "parameters": []}],
                environment="dev",
                operation="PrePull",
            )

        tm1_service.processes.delete.assert_called_once_with(
            "tm1_git_py_prepull_dev_abc123"
        )
        build_mock.assert_called_once()

    @pytest.mark.parametrize(
        "operation",
        ["PrePull", "PostPull", "PrePush", "PostPush"],
    )
    def test_apply_deployment_hook_operations_delegates_to_helpers(
        self, mocker, operation
    ):
        load_mock = mocker.patch(
            "tm1_git_py.services.apply._load_tm1project_json",
            return_value=(Path("/tmp/tm1project.json"), {"Deployment": {}}),
        )
        resolve_mock = mocker.patch(
            "tm1_git_py.services.apply._resolve_operation_process_specs",
            return_value=[{"process_name": "ProcA", "parameters": []}],
        )
        run_mock = mocker.patch(
            "tm1_git_py.services.apply._create_and_run_temp_process",
            return_value="ok",
        )
        tm1_service = mocker.Mock()

        result = _apply_deployment_hook_operations(
            tm1_service=tm1_service,
            project_file_path="tm1project.json",
            environment="dev",
            operation=operation,
            timeout=10,
        )

        load_mock.assert_called_once_with("tm1project.json")
        resolve_mock.assert_called_once_with(
            project_json={"Deployment": {}},
            environment="dev",
            operation=operation,
            project_path=Path("/tmp/tm1project.json"),
            parameter_overrides=None,
        )
        run_mock.assert_called_once_with(
            tm1_service=tm1_service,
            process_specs=[{"process_name": "ProcA", "parameters": []}],
            environment="dev",
            operation=operation,
            timeout=10,
        )
        assert result == "ok"

    def test_apply_deployment_hook_operations_accepts_process_specs_directly(
        self, mocker
    ):
        run_mock = mocker.patch(
            "tm1_git_py.services.apply._create_and_run_temp_process",
            return_value="ok",
        )
        tm1_service = mocker.Mock()
        process_json = {
            "Deployment": {"dev": {"PrePull": ["Tasks('TaskA')"]}},
            "Tasks": {"TaskA": {"Process": "Processes('ProcA')"}},
        }

        result = _apply_deployment_hook_operations(
            tm1_service=tm1_service,
            environment="dev",
            operation="PrePull",
            project_json=process_json,
            timeout=10,
        )

        run_mock.assert_called_once_with(
            tm1_service=tm1_service,
            process_specs=[
                {"task_name": "TaskA", "process_name": "ProcA", "parameters": {}}
            ],
            environment="dev",
            operation="PrePull",
            timeout=10,
        )
        assert result == "ok"

    def test_get_deployment_hook_operations_from_json_validates_and_resolves(
        self,
    ):
        project_json = {
            "Deployment": {"dev": {"PrePull": ["Tasks('TaskA')"]}},
            "Tasks": {"TaskA": {"Process": "Processes('ProcA')"}},
        }

        result = _resolve_operation_process_specs(
            project_json=project_json,
            environment="dev",
            operation="PrePull",
        )

        assert result == [
            {"task_name": "TaskA", "process_name": "ProcA", "parameters": {}}
        ]

    @pytest.mark.parametrize(
        "process_json,match",
        [
            (None, "Pass either project_file_path for a valid tm1project.json file"),
            ([], "tm1project JSON body must be a JSON object"),
        ],
    )
    def test_apply_deployment_hook_operations_rejects_invalid_input(
        self, mocker, process_json, match
    ):
        mocker.patch("tm1_git_py.services.apply._create_and_run_temp_process")
        tm1_service = mocker.Mock()

        with pytest.raises(ValueError, match=match):
            _apply_deployment_hook_operations(
                tm1_service=tm1_service,
                environment="dev",
                operation="PrePull",
                project_json=process_json,
            )


class TestHookParameterOverrides:
    def _specs(self, parameters=None):
        return [
            {
                "task_name": "TaskA",
                "process_name": "ProcA",
                "parameters": parameters
                if parameters is not None
                else [
                    {"Name": "pRun", "Value": "0"},
                    {"name": "pMode", "value": "fast"},
                ],
            }
        ]

    def test_merge_normalises_project_file_parameters(self):
        assert _merge_parameter_overrides(self._specs()) == [
            {
                "task_name": "TaskA",
                "process_name": "ProcA",
                "parameters": {"pRun": "0", "pMode": "fast"},
            }
        ]

    def test_override_replaces_adds_and_keeps(self, caplog):
        caplog.set_level("INFO", logger="tm1_git_py.services.apply")

        merged = _merge_parameter_overrides(
            self._specs(), {"TaskA": {"PRUN": "secret", "pNew": 2}}
        )

        parameters = merged[0]["parameters"]
        assert parameters == {"pRun": "secret", "pMode": "fast", "pNew": 2}
        assert list(parameters) == ["pRun", "pMode", "pNew"]
        assert "'pRun'" in caplog.text
        assert "secret" not in caplog.text

    def test_merge_does_not_change_its_input(self):
        specs = self._specs()

        _merge_parameter_overrides(specs, {"TaskA": {"pRun": 1}})

        assert specs == self._specs()

    def test_override_for_unknown_task_raises(self):
        with pytest.raises(ValueError, match="'Other'"):
            _merge_parameter_overrides(self._specs(), {"Other": {"pRun": 1}})

    def test_override_applies_to_the_named_task_only(self):
        specs = [
            {"task_name": "TaskA", "process_name": "ProcA", "parameters": []},
            {"task_name": "TaskB", "process_name": "ProcA", "parameters": []},
        ]

        merged = _merge_parameter_overrides(specs, {"TaskB": {"pRun": 1}})

        assert [spec["parameters"] for spec in merged] == [{}, {"pRun": 1}]

    @pytest.mark.parametrize("value", ["1", 1, 1.5])
    def test_string_and_number_values_are_accepted(self, value):
        merged = _merge_parameter_overrides(
            self._specs([{"Name": "pRun", "Value": value}])
        )

        assert merged[0]["parameters"] == {"pRun": value}

    @pytest.mark.parametrize("value", [True, False, {"a": 1}, [1]])
    def test_other_json_values_are_rejected(self, value):
        with pytest.raises(ValueError, match="TaskA.*pRun"):
            _merge_parameter_overrides(
                self._specs([{"Name": "pRun", "Value": value}])
            )
        with pytest.raises(ValueError, match="TaskA.*pRun"):
            _merge_parameter_overrides(self._specs([]), {"TaskA": {"pRun": value}})

    @pytest.mark.parametrize(
        "parameters",
        [
            ["pRun=1"],
            [{"Value": "1"}],
            [{"Name": "pRun", "Value": "1"}, {"Name": "PRUN", "Value": "2"}],
            {"Name": "pRun", "Value": {"nested": 1}},
            "pRun=1",
        ],
    )
    def test_malformed_project_file_parameters_are_rejected(self, parameters):
        with pytest.raises(ValueError, match="TaskA"):
            _merge_parameter_overrides(self._specs(parameters))

    def test_get_operations_previews_merged_parameters(self, tmp_path):
        project_path = tmp_path / "tm1project.json"
        project_path.write_text(json.dumps(PRUN_PROJECT), encoding="utf-8")

        specs = get_pre_pull_operations(
            project_file_path=project_path,
            environment="prod",
            parameter_overrides={"DropRules": {"pRun": 1}, "CleanCubes": {"pRun": 1}},
        )

        assert [(spec["task_name"], spec["parameters"]) for spec in specs] == [
            ("DropRules", {"pRun": 1}),
            ("CleanCubes", {"pRun": 1}),
        ]

    def test_override_without_tasks_in_phase_raises(self):
        with pytest.raises(ValueError, match="'DropRules'"):
            _apply_deployment_hook_operations(
                tm1_service=None,
                environment="prod",
                operation="PostPush",
                project_json=PRUN_PROJECT,
                parameter_overrides={"DropRules": {"pRun": 1}},
            )


class TestHookParameterTypes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1", "1"),
            ("1.5", "1.5"),
            (1, "1"),
            (1.5, "1.5"),
            ("1.0", "1"),
            ("-2", "-2"),
            (" 3 ", "3"),
            (0.0000001, "0.0000001"),
            ("1e3", "1000"),
        ],
    )
    def test_ti_numeric_literal(self, value, expected):
        assert _ti_numeric_literal(value) == expected

    @pytest.mark.parametrize("value", ["abc", "", "nan", "inf", "-inf", True])
    def test_ti_numeric_literal_rejects(self, value):
        with pytest.raises(ValueError):
            _ti_numeric_literal(value)

    @pytest.mark.parametrize(
        "param_type,value,expected",
        [
            ("Numeric", "1", "1"),
            ("Numeric", "1.5", "1.5"),
            ("Numeric", 1, "1"),
            ("Numeric", 1.5, "1.5"),
            ("String", "abc", "'abc'"),
            ("String", 1, "'1'"),
            ("String", "it's", "'it''s'"),
            ("String", "", "''"),
        ],
    )
    def test_statement_follows_declared_type(self, param_type, value, expected):
        assert (
            _build_execute_process_statement("ProcA", [("pValue", value, param_type)])
            == f"ExecuteProcess('ProcA', 'pValue', {expected});"
        )

    def test_resolve_declared_parameters_reads_each_process_once(self, mocker):
        tm1_service = _tm1_service_with_processes(mocker, PRUN_DECLARED)

        declared = _resolve_declared_parameters(
            tm1_service,
            [
                {"task_name": "A", "process_name": "Clear Rules"},
                {"task_name": "B", "process_name": "Clear Data"},
                {"task_name": "C", "process_name": "Clear Rules"},
            ],
        )

        assert declared == {
            "Clear Rules": {
                "prun": ("pRun", "Numeric"),
                "pmode": ("pMode", "String"),
            },
            "Clear Data": {"prun": ("pRun", "Numeric")},
        }
        assert tm1_service.processes.get.call_count == 2

    def test_resolve_declared_parameters_derives_missing_type(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.get.return_value.parameters = [
            {"Name": "pRun", "Value": 0},
            {"Name": "pMode", "Value": ""},
        ]

        declared = _resolve_declared_parameters(
            tm1_service, [{"task_name": "A", "process_name": "ProcA"}]
        )

        assert declared == {
            "ProcA": {"prun": ("pRun", "Numeric"), "pmode": ("pMode", "String")}
        }

    def test_missing_process_raises_naming_the_task(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.get.side_effect = TM1pyRestException(
            "not found", 404, "Not Found", {}
        )

        with pytest.raises(ValueError, match="Task 'A'.*'ProcA'.*does not exist"):
            _resolve_declared_parameters(
                tm1_service, [{"task_name": "A", "process_name": "ProcA"}]
            )

    def test_other_rest_errors_are_not_hidden(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.get.side_effect = TM1pyRestException(
            "boom", 500, "Server Error", {}
        )

        with pytest.raises(TM1pyRestException):
            _resolve_declared_parameters(
                tm1_service, [{"task_name": "A", "process_name": "ProcA"}]
            )

    def test_typed_specs_use_declared_spelling(self):
        typed = _apply_declared_parameter_types(
            [
                {
                    "task_name": "A",
                    "process_name": "ProcA",
                    "parameters": {"PRUN": "1", "pmode": 7},
                }
            ],
            {"ProcA": {"prun": ("pRun", "Numeric"), "pmode": ("pMode", "String")}},
        )

        assert typed[0]["parameters"] == [
            ("pRun", "1", "Numeric"),
            ("pMode", 7, "String"),
        ]

    @pytest.mark.parametrize("value", ["abc", ""])
    def test_non_numeric_value_error_names_everything_but_the_value(self, value):
        with pytest.raises(ValueError) as excinfo:
            _apply_declared_parameter_types(
                [
                    {
                        "task_name": "TaskA",
                        "process_name": "ProcA",
                        "parameters": {"prun": value},
                    }
                ],
                {"ProcA": {"prun": ("pRun", "Numeric")}},
            )

        message = str(excinfo.value)
        for expected in ("TaskA", "ProcA", "pRun", "Numeric"):
            assert expected in message
        assert "abc" not in message

    def test_run_with_overrides_builds_numeric_statements(self, mocker):
        tm1_service = _tm1_service_with_processes(mocker, PRUN_DECLARED)
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")

        apply_pre_pull_operations(
            tm1_service=tm1_service,
            environment="prod",
            project_json=PRUN_PROJECT,
            parameter_overrides={
                "DropRules": {"pRun": "1", "pMode": "Unload"},
                "CleanCubes": {"pRun": 1},
            },
            timeout=30,
        )

        assert tm1py_process_cls.call_args.kwargs["prolog_procedure"] == (
            "ExecuteProcess('Clear Rules', 'pRun', 1, 'pMode', 'Unload');\n"
            "ExecuteProcess('Clear Data', 'pRun', 1);"
        )
        # parameter_overrides must not reach TM1py through **kwargs.
        tm1_service.processes.execute.assert_called_once_with(
            process_name=tm1py_process_cls.call_args.kwargs["name"],
            parameters={},
            timeout=30,
        )

    @pytest.mark.parametrize("file_value", ["1", 1])
    def test_json_string_and_number_give_the_same_statement(self, mocker, file_value):
        tm1_service = _tm1_service_with_processes(mocker, PRUN_DECLARED)
        tm1py_process_cls = mocker.patch("tm1_git_py.services.apply.TM1pyProcess")
        project = json.loads(json.dumps(PRUN_PROJECT))
        project["Tasks"]["DropRules"]["Parameters"][0]["Value"] = file_value

        apply_pre_pull_operations(
            tm1_service=tm1_service, environment="prod", project_json=project
        )

        assert tm1py_process_cls.call_args.kwargs["prolog_procedure"] == (
            "ExecuteProcess('Clear Rules', 'pRun', 1);\nExecuteProcess('Clear Data');"
        )

    @pytest.mark.parametrize(
        "overrides,match",
        [
            ({"CleanCubes": {"pMissing": 1}}, "CleanCubes.*no parameter 'pMissing'"),
            ({"CleanCubes": {"pRun": "yes"}}, "CleanCubes.*pRun.*Numeric"),
        ],
    )
    def test_bad_override_raises_before_anything_is_created(
        self, mocker, overrides, match
    ):
        tm1_service = _tm1_service_with_processes(mocker, PRUN_DECLARED)

        with pytest.raises(ValueError, match=match):
            apply_pre_pull_operations(
                tm1_service=tm1_service,
                environment="prod",
                project_json=PRUN_PROJECT,
                parameter_overrides=overrides,
            )

        tm1_service.processes.create.assert_not_called()
        tm1_service.processes.execute.assert_not_called()

    def test_undeclared_project_file_parameter_raises(self, mocker):
        tm1_service = _tm1_service_with_processes(mocker, {"Clear Data": []})
        project = json.loads(json.dumps(PRUN_PROJECT))
        project["Deployment"]["prod"]["PrePull"] = ["Tasks('DropRules')"]

        with pytest.raises(ValueError, match="DropRules.*no parameter 'pRun'"):
            apply_pre_pull_operations(
                tm1_service=tm1_service, environment="prod", project_json=project
            )

        tm1_service.processes.create.assert_not_called()


WRAPPER = "tm1_git_py_prepull_prod_abc123"
OTHER_WRAPPER = "tm1_git_py_prepull_prod_earlier"
HOOK_SPECS = [
    {"task_name": "DropRules", "process_name": "Clear Rules", "parameters": {}},
    {"task_name": "CleanCubes", "process_name": "Clear Data", "parameters": {}},
]


def _log_entry(message, level="Info", logger="TM1.Process", thread=1):
    return {
        "ThreadID": thread,
        "Level": level,
        "TimeStamp": "2026-10-13T13:58:42.649Z",
        "Logger": logger,
        "Message": message,
    }


def _run_log(wrapper=WRAPPER, first=(), second=(), finished="with errors", thread=1):
    """Log block of one wrapper run with two tasks, as the server writes it."""
    return [
        _log_entry(f'Process "{wrapper}" executed by user "Admin"', thread=thread),
        _log_entry(
            f'Process "Clear Rules" run from process "{wrapper}" by user "Admin"',
            thread=thread,
        ),
        *first,
        _log_entry(
            f'Process "Clear Data" run from process "{wrapper}" by user "Admin"',
            thread=thread,
        ),
        *second,
        _log_entry(
            f'Process "{wrapper}":  finished executing {finished}.',
            level="Error" if finished == "with errors" else "Info",
            thread=thread,
        ),
    ]


def _quit_entry(process_name):
    return _log_entry(
        f'Process "{process_name}": : Execution was aborted by ProcessQuit Function.',
        level="Error",
    )


def _ti_error_entry(message="Start process with flag = 1 to run."):
    return _log_entry(message, level="Error", logger="TM1.TILogOutput")


class TestHookFailureCheck:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, mocker):
        return mocker.patch("tm1_git_py.services.apply.time.sleep")

    def test_logged_error_is_a_failure_of_its_task(self):
        entries = _run_log(
            first=[_ti_error_entry(), _quit_entry("Clear Rules")],
            second=[_ti_error_entry("second"), _quit_entry("Clear Data")],
        )
        run_entries, finished = apply_module._select_hook_run_entries(entries, WRAPPER)

        failures = apply_module._find_hook_failures(run_entries, WRAPPER, HOOK_SPECS)

        assert finished is True
        assert [
            (failure["task_name"], failure["process_name"], failure["message"])
            for failure in failures
        ] == [
            ("DropRules", "Clear Rules", "Start process with flag = 1 to run."),
            ("CleanCubes", "Clear Data", "second"),
        ]

    def test_quit_without_logged_error_is_not_a_failure(self):
        entries = _run_log(
            first=[_quit_entry("Clear Rules")], second=[_quit_entry("Clear Data")]
        )
        run_entries, _ = apply_module._select_hook_run_entries(entries, WRAPPER)

        assert apply_module._find_hook_failures(run_entries, WRAPPER, HOOK_SPECS) == []

    def test_real_ti_error_is_a_failure(self):
        message = 'Process "Clear Data":  finished executing with errors.'
        entries = _run_log(second=[_log_entry(message, level="Error")])
        run_entries, _ = apply_module._select_hook_run_entries(entries, WRAPPER)

        failures = apply_module._find_hook_failures(run_entries, WRAPPER, HOOK_SPECS)

        assert [(f["task_name"], f["message"]) for f in failures] == [
            ("CleanCubes", message)
        ]

    def test_nested_process_error_stays_with_its_task(self):
        nested = _log_entry(
            'Process "Helper" run from process "Clear Rules" by user "Admin"'
        )
        entries = _run_log(first=[nested, _ti_error_entry("from helper")])
        run_entries, _ = apply_module._select_hook_run_entries(entries, WRAPPER)

        failures = apply_module._find_hook_failures(run_entries, WRAPPER, HOOK_SPECS)

        assert [(f["task_name"], f["process_name"]) for f in failures] == [
            ("DropRules", "Clear Rules")
        ]

    def test_entries_of_other_runs_and_threads_are_ignored(self):
        entries = [
            *_run_log(wrapper=OTHER_WRAPPER, first=[_ti_error_entry("earlier run")]),
            *_run_log()[:2],
            _log_entry("other thread", level="Error", thread=2),
            *_run_log(finished="normally")[2:],
            _ti_error_entry("after the run"),
        ]

        run_entries, finished = apply_module._select_hook_run_entries(entries, WRAPPER)

        assert finished is True
        assert apply_module._find_hook_failures(run_entries, WRAPPER, HOOK_SPECS) == []

    def test_run_without_opening_entry_is_not_selected(self):
        entries = _run_log(wrapper=OTHER_WRAPPER, first=[_ti_error_entry()])

        assert apply_module._select_hook_run_entries(entries, WRAPPER) == (None, False)

    def test_log_is_read_again_until_the_run_is_closed(self, mocker, _no_sleep):
        complete = _run_log(first=[_ti_error_entry()])
        tm1_service = mocker.Mock()
        tm1_service.server.get_message_log_entries.side_effect = [
            complete[:3],
            complete,
        ]
        since = mocker.sentinel.since

        failures = apply_module._collect_hook_failures(
            tm1_service, WRAPPER, HOOK_SPECS, since
        )

        assert len(failures) == 1
        assert _no_sleep.call_count == 1
        tm1_service.server.get_message_log_entries.assert_called_with(
            reverse=False, since=since
        )

    def test_start_time_is_the_newest_server_entry(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.server.get_message_log_entries.return_value = [_log_entry("x")]

        start = apply_module._hook_log_start_time(tm1_service)

        assert start.isoformat() == "2026-10-13T13:58:42.649000+00:00"
        tm1_service.server.get_message_log_entries.assert_called_once_with(
            reverse=True, top=1
        )

    def test_start_time_falls_back_to_client_clock(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.server.get_message_log_entries.return_value = []

        start = apply_module._hook_log_start_time(tm1_service)

        assert start.tzinfo is not None

    def _run(self, mocker, log_entries):
        tm1_service = _tm1_service_with_processes(mocker)
        tm1_service.processes.execute.return_value = "executed"
        tm1_service.server.get_message_log_entries.side_effect = log_entries
        uuid_mock = mocker.patch("tm1_git_py.services.apply.uuid.uuid4")
        uuid_mock.return_value.hex = "abc123"
        return tm1_service, lambda: _create_and_run_temp_process(
            tm1_service=tm1_service,
            process_specs=HOOK_SPECS,
            environment="prod",
            operation="PrePull",
        )

    def test_hook_raises_after_all_tasks_ran_and_cleans_up(self, mocker):
        run_log = _run_log(first=[_ti_error_entry()], second=[_ti_error_entry("two")])
        tm1_service, run = self._run(mocker, [[], run_log])

        with pytest.raises(apply_module.DeploymentHookError) as excinfo:
            run()

        assert [f["task_name"] for f in excinfo.value.failures] == [
            "DropRules",
            "CleanCubes",
        ]
        assert excinfo.value.response == "executed"
        assert "task 'DropRules' (process 'Clear Rules')" in str(excinfo.value)
        tm1_service.processes.delete.assert_called_once_with(WRAPPER)

    def test_hook_passes_when_tasks_only_quit(self, mocker):
        run_log = _run_log(first=[_quit_entry("Clear Rules")])
        tm1_service, run = self._run(mocker, [[], run_log])

        assert run() == "executed"
        tm1_service.processes.delete.assert_called_once_with(WRAPPER)

    def test_unreadable_log_does_not_fail_the_hook(self, mocker, caplog):
        tm1_service, run = self._run(mocker, [[], RuntimeError("no access")])

        with caplog.at_level("WARNING"):
            assert run() == "executed"

        assert "were not checked" in caplog.text
        tm1_service.processes.delete.assert_called_once_with(WRAPPER)

from pathlib import Path
from types import SimpleNamespace

import pytest
from TM1py import TM1Service

import tm1_git_py.services.apply as apply_module
from tm1_git_py.services.apply import (
    apply_post_pull_operations,
    apply_pre_pull_operations,
)


@pytest.mark.usefixtures("tm1_service")
class TestApplyDeploymentHookOperationsIntegration:
    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    @pytest.mark.parametrize(
        "operation,apply_fn,temp_prefix,expected_process_names",
        [
            (
                "PrePull",
                apply_pre_pull_operations,
                "tm1_git_py_prepull_localhost_",
                ["myprocess", "myprocess2"],
            ),
            (
                "PostPull",
                apply_post_pull_operations,
                "tm1_git_py_postpull_localhost_",
                ["myprocess3"],
            ),
        ],
    )
    def test_creates_executes_and_deletes_temp_process(
        self,
        monkeypatch,
        operation,
        apply_fn,
        temp_prefix,
        expected_process_names,
    ):
        project_path = Path(__file__).with_name("tm1project.json")
        temp_process_name = f"{temp_prefix}abc123"

        original_execute = self.tm1_service.processes.execute
        original_delete = self.tm1_service.processes.delete
        inspected: dict[str, str] = {}
        deleted: list[str] = []

        def execute_and_inspect(*args, **kwargs):
            process_name = kwargs.get("process_name") or args[0]
            temp_process = self.tm1_service.processes.get(process_name)
            inspected["name"] = process_name
            inspected["prolog_procedure"] = getattr(
                temp_process, "prolog_procedure", ""
            )
            return original_execute(*args, **kwargs)

        def delete_and_record(process_name, *args, **kwargs):
            deleted.append(process_name)
            return original_delete(process_name, *args, **kwargs)

        monkeypatch.setattr(self.tm1_service.processes, "execute", execute_and_inspect)
        monkeypatch.setattr(self.tm1_service.processes, "delete", delete_and_record)
        monkeypatch.setattr(
            apply_module.uuid,
            "uuid4",
            lambda: SimpleNamespace(hex="abc123"),
        )

        response = apply_fn(
            tm1_service=self.tm1_service,
            project_file_path=project_path,
            environment="localhost",
            timeout=30,
        )

        assert getattr(response, "status_code", None) in (200, 201, 204)
        assert inspected["name"] == temp_process_name
        for process_name in expected_process_names:
            assert f"ExecuteProcess('{process_name}');" in inspected["prolog_procedure"]
        assert deleted == [temp_process_name]
        assert not self.tm1_service.processes.exists(temp_process_name)

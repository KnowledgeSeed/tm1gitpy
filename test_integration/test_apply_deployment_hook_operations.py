import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from TM1py import Process, TM1Service

import tm1_git_py.services.apply as apply_module
from tm1_git_py.services.apply import (
    apply_post_pull_operations,
    apply_pre_pull_operations,
)

TM1_DATA_DIR = "/docker-entrypoint-initdb.d/tm1models/24Retail"
TM1_CONTAINER_CANDIDATES = ("test_integration-tm1-1", "tm1-rocky9")
TM1_SERVER_LOG_TIMEOUT_SECONDS = 5
# my_migration of the fixture model logs this, followed by pMarker, when it
# runs. Unless pRun = 1 it logs MIGRATION_GUARD_MESSAGE as ERROR and quits.
MIGRATION_PROCESS = "my_migration"
MIGRATION_RAN_MESSAGE = "tm1gitpy_it guarded ran"
MIGRATION_GUARD_MESSAGE = "Start process with flag = 1 to run."


def _tm1_container_name() -> str:
    for container_name in TM1_CONTAINER_CANDIDATES:
        result = subprocess.run(
            ["docker", "exec", container_name, "true"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return container_name
    raise AssertionError(
        "Could not find a running TM1 container. Tried: "
        + ", ".join(TM1_CONTAINER_CANDIDATES)
    )


def _docker_exec_tm1(*command: str) -> str:
    result = subprocess.run(
        ["docker", "exec", _tm1_container_name(), *command],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _read_tm1_file_since(path: str, start_size: int) -> str:
    script = (
        f"file={path!r}; "
        f"start_size={start_size}; "
        'current_size=$(stat -c %s "$file"); '
        'if [ "$current_size" -ge "$start_size" ]; then '
        'tail -c +$((start_size + 1)) "$file"; '
        "else "
        'cat "$file"; '
        "fi"
    )
    return _docker_exec_tm1("bash", "-lc", script)


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
                ["my_migration"],
            ),
            (
                "PostPull",
                apply_post_pull_operations,
                "tm1_git_py_postpull_localhost_",
                ["my_migration"],
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
            assert (
                f"ExecuteProcess('{process_name}', 'pRun', 1);"
                in inspected["prolog_procedure"]
            )
        assert deleted == [temp_process_name]
        assert not self.tm1_service.processes.exists(temp_process_name)

    def test_pre_and_post_pull_hooks_write_tm1_logs(self, monkeypatch):
        project_path = Path(__file__).with_name("tm1project.json")
        tm1server_log_path = f"{TM1_DATA_DIR}/tm1server.log"

        tm1server_log_size = int(
            _docker_exec_tm1("stat", "-c", "%s", tm1server_log_path).strip()
        )

        monkeypatch.setattr(
            apply_module.uuid,
            "uuid4",
            lambda: SimpleNamespace(hex="abc123"),
        )

        pre_response = apply_pre_pull_operations(
            tm1_service=self.tm1_service,
            project_file_path=project_path,
            environment="localhost",
            timeout=30,
        )
        post_response = apply_post_pull_operations(
            tm1_service=self.tm1_service,
            project_file_path=project_path,
            environment="localhost",
            timeout=30,
        )

        assert getattr(pre_response, "status_code", None) in (200, 201, 204)
        assert getattr(post_response, "status_code", None) in (200, 201, 204)

        expected_log_lines = [
            'Process "tm1_git_py_prepull_localhost_abc123" executed by user "Admin"',
            'Process "my_migration" run from process '
            '"tm1_git_py_prepull_localhost_abc123" by user "Admin"',
            MIGRATION_RAN_MESSAGE,
            'Process "my_migration":  finished executing normally',
            'Process "tm1_git_py_prepull_localhost_abc123":  '
            "finished executing normally",
            'Process "tm1_git_py_postpull_localhost_abc123" executed by user "Admin"',
            'Process "my_migration" run from process '
            '"tm1_git_py_postpull_localhost_abc123" by user "Admin"',
            MIGRATION_RAN_MESSAGE,
            'Process "my_migration":  finished executing normally',
            'Process "tm1_git_py_postpull_localhost_abc123":  '
            "finished executing normally",
        ]
        # tm1server.log is flushed asynchronously (about once per second),
        # so wait for the last expected line instead of reading it once.
        deadline = time.monotonic() + TM1_SERVER_LOG_TIMEOUT_SECONDS
        while True:
            tm1server_log = _read_tm1_file_since(
                tm1server_log_path, tm1server_log_size
            )
            if (
                expected_log_lines[-1] in tm1server_log
                or time.monotonic() > deadline
            ):
                break
            time.sleep(0.25)

        search_from = 0
        for expected_line in expected_log_lines:
            found_at = tm1server_log.find(expected_line, search_from)
            assert found_at != -1, f"Missing tm1server.log line: {expected_line}"
            search_from = found_at + len(expected_line)


HOOK_ENVIRONMENT = "it"
HOOK_PROCESS_PREFIX = "tm1gitpy_it_hook_"
QUIT_PROCESS = f"{HOOK_PROCESS_PREFIX}quit"
RUNTIME_ERROR_PROCESS = f"{HOOK_PROCESS_PREFIX}runtime_error"


def _hook_fixture_processes() -> list[Process]:
    """Processes for the cases my_migration does not cover.

    The tests create them and delete them afterwards, so the model keeps only
    my_migration.
    """
    return [
        Process(
            name=QUIT_PROCESS,
            prolog_procedure=(
                "LogOutput('INFO', 'tm1gitpy_it quit without error');\nProcessQuit;"
            ),
        ),
        Process(
            name=RUNTIME_ERROR_PROCESS,
            prolog_procedure="CellPutN(1, 'tm1gitpy_it_no_such_cube', 'x');",
        ),
    ]


def _migration_parameters(marker: str, run_value: object = None) -> list[dict]:
    parameters = [{"Name": "pMarker", "Value": marker}]
    if run_value is not None:
        parameters.insert(0, {"Name": "pRun", "Value": run_value})
    return parameters


def _hook_project(*tasks: tuple[str, str, Optional[list[dict]]]) -> dict:
    """tm1project.json body whose PrePull phase runs the given tasks in order."""
    task_entries = {}
    for task_name, process_name, parameters in tasks:
        task_entries[task_name] = {"Process": f"Processes('{process_name}')"}
        if parameters is not None:
            task_entries[task_name]["Parameters"] = parameters
    return {
        "Version": "1.0",
        "Tasks": task_entries,
        "Deployment": {
            HOOK_ENVIRONMENT: {
                "PrePull": [f"Tasks('{task_name}')" for task_name, _, _ in tasks]
            }
        },
    }


@pytest.mark.usefixtures("tm1_service")
class TestDeploymentHookParametersAndFailuresIntegration:
    @pytest.fixture(autouse=True, scope="class")
    def _hook_processes(self, tm1_service):
        processes = _hook_fixture_processes()
        for process in processes:
            if tm1_service.processes.exists(process.name):
                tm1_service.processes.delete(process.name)
            tm1_service.processes.create(process)
        yield
        for process in processes:
            if tm1_service.processes.exists(process.name):
                tm1_service.processes.delete(process.name)

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service
        yield
        assert self._temp_process_names() == []

    def _temp_process_names(self) -> list[str]:
        prefix = f"tm1_git_py_prepull_{HOOK_ENVIRONMENT}_"
        return [
            name
            for name in self.tm1_service.processes.get_all_names()
            if name.startswith(prefix)
        ]

    def _run(self, project_json: dict, **kwargs):
        return apply_pre_pull_operations(
            tm1_service=self.tm1_service,
            environment=HOOK_ENVIRONMENT,
            project_json=project_json,
            timeout=30,
            **kwargs,
        )

    def _migration_ran(self, marker: str) -> bool:
        entries = self.tm1_service.server.get_message_log_entries(
            msg_contains=[f"{MIGRATION_RAN_MESSAGE} {marker}"]
        )
        return bool(entries)

    @staticmethod
    def _marker() -> str:
        return uuid.uuid4().hex

    @pytest.mark.parametrize("run_value", ["1", 1], ids=["string", "number"])
    def test_numeric_parameter_from_project_file_starts_migration(self, run_value):
        marker = self._marker()
        project = _hook_project(
            ("Migration", MIGRATION_PROCESS, _migration_parameters(marker, run_value))
        )

        response = self._run(project)

        assert getattr(response, "status_code", None) in (200, 201, 204)
        assert self._migration_ran(marker)

    def test_logged_error_then_quit_fails_the_hook(self):
        # Without pRun, my_migration logs an ERROR and quits.
        marker = self._marker()
        project = _hook_project(
            ("Migration", MIGRATION_PROCESS, _migration_parameters(marker))
        )

        with pytest.raises(apply_module.DeploymentHookError) as excinfo:
            self._run(project)

        assert [
            (failure["task_name"], failure["process_name"], failure["message"])
            for failure in excinfo.value.failures
        ] == [("Migration", MIGRATION_PROCESS, MIGRATION_GUARD_MESSAGE)]
        assert not self._migration_ran(marker)

    def test_command_line_override_takes_precedence_over_project_file(self):
        marker = self._marker()
        project = _hook_project(
            ("Migration", MIGRATION_PROCESS, _migration_parameters(marker, "0"))
        )

        self._run(project, parameter_overrides={"Migration": {"prun": "1"}})

        assert self._migration_ran(marker)

    def test_quit_without_logged_error_passes(self):
        project = _hook_project(("QuitOnly", QUIT_PROCESS, None))

        response = self._run(project)

        assert getattr(response, "status_code", None) in (200, 201, 204)

    def test_ti_runtime_error_fails_the_hook(self):
        project = _hook_project(("Failing", RUNTIME_ERROR_PROCESS, None))

        with pytest.raises(apply_module.DeploymentHookError) as excinfo:
            self._run(project)

        assert len(excinfo.value.failures) == 1
        failure = excinfo.value.failures[0]
        assert failure["task_name"] == "Failing"
        assert failure["process_name"] == RUNTIME_ERROR_PROCESS
        assert 'Cube "tm1gitpy_it_no_such_cube" Not Found' in failure["message"]

    def test_later_task_runs_after_an_earlier_task_failed(self):
        failed_marker = self._marker()
        marker = self._marker()
        project = _hook_project(
            ("Failing", MIGRATION_PROCESS, _migration_parameters(failed_marker)),
            ("Migration", MIGRATION_PROCESS, _migration_parameters(marker, "1")),
        )

        with pytest.raises(apply_module.DeploymentHookError) as excinfo:
            self._run(project)

        assert [failure["task_name"] for failure in excinfo.value.failures] == [
            "Failing"
        ]
        assert not self._migration_ran(failed_marker)
        assert self._migration_ran(marker)

    def test_undeclared_parameter_stops_before_the_wrapper_is_created(
        self, monkeypatch
    ):
        project = _hook_project(
            ("Migration", MIGRATION_PROCESS, _migration_parameters(self._marker(), "1"))
        )
        created: list[str] = []
        original_create = self.tm1_service.processes.create

        def create_and_record(process, *args, **kwargs):
            created.append(process.name)
            return original_create(process, *args, **kwargs)

        monkeypatch.setattr(self.tm1_service.processes, "create", create_and_record)

        with pytest.raises(ValueError, match="pNotDeclared"):
            self._run(
                project, parameter_overrides={"Migration": {"pNotDeclared": "x"}}
            )

        assert created == []

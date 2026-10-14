import argparse

import pytest

import tm1_git_py.main as main_module


@pytest.mark.parametrize(
    ("deployment", "source_control", "runner_name"),
    [
        ("pre", None, "apply_pre_pull_operations"),
        ("post", None, "apply_post_pull_operations"),
        (None, "pre", "apply_pre_push_operations"),
        (None, "post", "apply_post_push_operations"),
    ],
)
def test_run_hook_selects_expected_runner(
    tmp_path,
    mocker,
    deployment,
    source_control,
    runner_name,
):
    project_path = tmp_path / "tm1project.json"
    project_path.write_text("{}", encoding="utf-8")
    tm1_service = mocker.sentinel.tm1_service
    mocker.patch.object(main_module, "_tm1_connection", return_value=tm1_service)
    runners = {
        name: mocker.patch.object(main_module, name)
        for name in (
            "apply_pre_pull_operations",
            "apply_post_pull_operations",
            "apply_pre_push_operations",
            "apply_post_push_operations",
        )
    }

    main_module._cmd_run_hook(
        argparse.Namespace(
            server="local",
            tm1project=f"file://{project_path}",
            environment="test",
            deployment=deployment,
            source_control=source_control,
        )
    )

    runners[runner_name].assert_called_once_with(
        tm1_service=tm1_service,
        environment="test",
        project_file_path=project_path.resolve(),
        parameter_overrides=None,
    )
    for name, runner in runners.items():
        if name != runner_name:
            runner.assert_not_called()


def test_run_hook_requires_exactly_one_phase(monkeypatch, tmp_path):
    project_path = tmp_path / "tm1project.json"
    project_path.write_text("{}", encoding="utf-8")
    base_args = [
        "tm1gitpy",
        "run-hook",
        "--server",
        "local",
        "--tm1project",
        str(project_path),
        "--environment",
        "test",
    ]

    for phase_args in ([], ["--deployment", "pre", "--source-control", "post"]):
        monkeypatch.setattr(main_module.sys, "argv", base_args + phase_args)
        with pytest.raises(SystemExit, match="2"):
            main_module.main()


def test_parse_hook_parameters_groups_by_task():
    assert main_module._parse_hook_parameters(
        [
            "PrePullDropRules.pRun=1",
            "PrePullDropRules.pMode=Unload",
            "PrePullCleanCubes.pRun=1",
            "Task.With.Dots.pFilter=a=b.c",
            "Backup.pTarget=",
        ]
    ) == {
        "PrePullDropRules": {"pRun": "1", "pMode": "Unload"},
        "PrePullCleanCubes": {"pRun": "1"},
        "Task.With.Dots": {"pFilter": "a=b.c"},
        "Backup": {"pTarget": ""},
    }


def test_parse_hook_parameters_empty():
    assert main_module._parse_hook_parameters([]) == {}


@pytest.mark.parametrize(
    "values",
    [
        ["PrePullDropRules.pRun"],
        ["pRun=1"],
        [".pRun=1"],
        ["PrePullDropRules.=1"],
        ["PrePullDropRules.pRun=1", "PrePullDropRules.PRUN=0"],
    ],
)
def test_parse_hook_parameters_rejects_invalid(values):
    with pytest.raises(ValueError):
        main_module._parse_hook_parameters(values)


def _run_hook_argv(project_path, *extra):
    return [
        "tm1gitpy",
        "run-hook",
        "--server",
        "local",
        "--tm1project",
        str(project_path),
        "--environment",
        "test",
        "--deployment",
        "pre",
        *extra,
    ]


def test_run_hook_passes_parameter_overrides(monkeypatch, mocker, tmp_path):
    project_path = tmp_path / "tm1project.json"
    project_path.write_text("{}", encoding="utf-8")
    mocker.patch.object(main_module, "setup_logging")
    mocker.patch.object(
        main_module, "_tm1_connection", return_value=mocker.sentinel.tm1_service
    )
    runner = mocker.patch.object(main_module, "apply_pre_pull_operations")
    monkeypatch.setattr(
        main_module.sys,
        "argv",
        _run_hook_argv(
            project_path,
            "-p",
            "PrePullDropRules.pRun=1",
            "--parameter",
            "PrePullCleanCubes.pRun=1",
        ),
    )

    main_module.main()

    assert runner.call_args.kwargs["parameter_overrides"] == {
        "PrePullDropRules": {"pRun": "1"},
        "PrePullCleanCubes": {"pRun": "1"},
    }


def test_run_hook_rejects_parameter_without_task(monkeypatch, mocker, tmp_path):
    project_path = tmp_path / "tm1project.json"
    project_path.write_text("{}", encoding="utf-8")
    runner = mocker.patch.object(main_module, "apply_pre_pull_operations")
    monkeypatch.setattr(
        main_module.sys, "argv", _run_hook_argv(project_path, "-p", "pRun=1")
    )

    with pytest.raises(SystemExit, match="2"):
        main_module.main()
    runner.assert_not_called()


def test_run_hook_exits_with_1_when_a_task_logged_an_error(
    monkeypatch, mocker, tmp_path, caplog
):
    project_path = tmp_path / "tm1project.json"
    project_path.write_text("{}", encoding="utf-8")
    mocker.patch.object(main_module, "setup_logging")
    mocker.patch.object(
        main_module, "_tm1_connection", return_value=mocker.sentinel.tm1_service
    )
    mocker.patch.object(
        main_module,
        "apply_pre_pull_operations",
        side_effect=main_module.DeploymentHookError(
            "PrePull",
            "prod",
            [
                {
                    "task_name": "PrePullDropRules",
                    "process_name": "Clear Rules",
                    "message": "Start process with flag = 1 to run.",
                }
            ],
        ),
    )
    monkeypatch.setattr(main_module.sys, "argv", _run_hook_argv(project_path))

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1
    assert "Task 'PrePullDropRules' (process 'Clear Rules') logged an error" in caplog.text

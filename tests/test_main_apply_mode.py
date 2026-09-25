import argparse

import pytest

import tm1_git_py.main as main_module
from tm1_git_py.main import _cmd_apply


class _FakeChangeset:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name, tm1_service, **kwargs):
        self.calls.append((name, {"tm1_service": tm1_service, **kwargs}))
        return True, None

    def apply(self, tm1_service, **kwargs):
        return self._record("apply", tm1_service, **kwargs)

    def apply_atomic(self, tm1_service, **kwargs):
        return self._record("apply_atomic", tm1_service, **kwargs)

    def apply_auto(self, tm1_service, **kwargs):
        return self._record("apply_auto", tm1_service, **kwargs)

    def close(self):
        pass


def _make_args(tmp_path, **overrides):
    changeset_path = tmp_path / "changeset.json"
    changeset_path.write_text("{}", encoding="utf-8")
    defaults = dict(
        changeset=str(changeset_path),
        server="TestServer",
        status_dir=None,
        execution_id=None,
        no_fail_fast=False,
        apply_mode="auto",
        max_atomic_body_kb=None,
        debug=False,
        log_file=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def fake_changeset(monkeypatch):
    changeset = _FakeChangeset()
    monkeypatch.setattr(main_module, "_tm1_connection", lambda server: f"tm1_service:{server}")
    monkeypatch.setattr(main_module, "import_changeset", lambda path: changeset)
    return changeset


class TestCmdApplyModeDispatch:
    def test_default_apply_mode_dispatches_through_apply_auto(self, tmp_path, fake_changeset):
        args = _make_args(tmp_path)

        _cmd_apply(args)

        assert [name for name, _ in fake_changeset.calls] == ["apply_auto"]

    def test_explicit_atomic_mode_calls_apply_atomic_directly(self, tmp_path, fake_changeset):
        args = _make_args(tmp_path, apply_mode="atomic")

        _cmd_apply(args)

        assert [name for name, _ in fake_changeset.calls] == ["apply_atomic"]

    def test_explicit_simple_mode_calls_apply_directly(self, tmp_path, fake_changeset):
        args = _make_args(tmp_path, apply_mode="simple")

        _cmd_apply(args)

        assert [name for name, _ in fake_changeset.calls] == ["apply"]

    def test_max_atomic_body_kb_is_forwarded_as_bytes_only_in_auto_mode(self, tmp_path, fake_changeset):
        args = _make_args(tmp_path, apply_mode="auto", max_atomic_body_kb=64)

        _cmd_apply(args)

        name, kwargs = fake_changeset.calls[0]
        assert name == "apply_auto"
        assert kwargs["max_body_bytes"] == 64 * 1024

    def test_max_atomic_body_kb_is_ignored_outside_auto_mode(self, tmp_path, fake_changeset):
        args = _make_args(tmp_path, apply_mode="simple", max_atomic_body_kb=64)

        _cmd_apply(args)

        name, kwargs = fake_changeset.calls[0]
        assert name == "apply"
        assert "max_body_bytes" not in kwargs

    def test_common_apply_kwargs_are_forwarded_regardless_of_mode(self, tmp_path, fake_changeset):
        args = _make_args(
            tmp_path,
            apply_mode="atomic",
            execution_id="exec-42",
            no_fail_fast=True,
        )

        _cmd_apply(args)

        _, kwargs = fake_changeset.calls[0]
        assert kwargs["tm1_service"] == "tm1_service:TestServer"
        assert kwargs["execution_id"] == "exec-42"
        assert kwargs["fail_fast"] is False


class TestApplyModeArgparsingThroughMain:
    """Exercises the real parser built inside main.main(), not a re-declared copy."""

    def _run_main(self, monkeypatch, argv):
        captured = {}

        def fake_handler(args):
            captured["args"] = args

        monkeypatch.setattr(main_module, "_cmd_apply", fake_handler)
        monkeypatch.setattr(main_module.sys, "argv", ["tm1gitpy"] + argv)
        main_module.main()
        return captured["args"]

    def test_apply_mode_defaults_to_auto(self, monkeypatch, tmp_path):
        changeset_path = tmp_path / "changeset.json"
        changeset_path.write_text("{}", encoding="utf-8")

        args = self._run_main(
            monkeypatch, ["apply", "-s", "TestServer", "-c", str(changeset_path)]
        )

        assert args.apply_mode == "auto"
        assert args.max_atomic_body_kb is None

    def test_apply_mode_rejects_unknown_choice(self, monkeypatch, tmp_path):
        changeset_path = tmp_path / "changeset.json"
        changeset_path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(main_module.sys, "argv", [
            "tm1gitpy", "apply", "-s", "TestServer", "-c", str(changeset_path),
            "--apply-mode", "bogus",
        ])

        with pytest.raises(SystemExit):
            main_module.main()

    def test_explicit_apply_mode_and_max_atomic_body_kb_are_parsed(self, monkeypatch, tmp_path):
        changeset_path = tmp_path / "changeset.json"
        changeset_path.write_text("{}", encoding="utf-8")

        args = self._run_main(
            monkeypatch,
            [
                "apply", "-s", "TestServer", "-c", str(changeset_path),
                "--apply-mode", "atomic", "--max-atomic-body-kb", "64",
            ],
        )

        assert args.apply_mode == "atomic"
        assert args.max_atomic_body_kb == 64

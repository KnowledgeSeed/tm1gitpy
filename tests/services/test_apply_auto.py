from tests.unit_common import *

import tm1_git_py.services.apply as apply_module
from tm1_git_py.services.apply import (
    ApplyStrategyDecision,
    AtomicApplySizeEstimate,
    apply_auto,
)


def _changeset_with_cube(name: str = "Cube_A") -> Changeset:
    cube = make_cube(name=name)
    changeset = Changeset()
    changeset.changes = [
        Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.CUBE,
            uri=Cube.uri_for(name),
            body=cube,
        )
    ]
    return changeset


def _fake_decision(strategy: str) -> ApplyStrategyDecision:
    estimate = AtomicApplySizeEstimate(
        process="fake-process",
        master_ti_code="fake-ti",
        body_bytes=123,
        ti_line_count=4,
    )
    return ApplyStrategyDecision(
        strategy=strategy,
        estimate=estimate,
        max_body_bytes=32 * 1024,
        max_ti_lines=15_000,
    )


class TestApplyAuto:
    def test_empty_changeset_short_circuits_without_choosing_a_strategy(self, monkeypatch):
        calls = []
        monkeypatch.setattr(apply_module, "choose_apply_strategy", lambda *a, **k: calls.append((a, k)))

        ok, changes = apply_auto(Changeset(), tm1_service=object())

        assert ok is True
        assert changes is None
        assert calls == []

    def test_atomic_decision_calls_apply_with_atomic_schema_with_reused_process(self, monkeypatch):
        changeset = _changeset_with_cube()
        decision = _fake_decision("atomic")
        monkeypatch.setattr(apply_module, "choose_apply_strategy", lambda *a, **k: decision)

        captured = {}

        def fake_apply_with_atomic_schema(**kwargs):
            captured.update(kwargs)
            return True, ["ok"]

        monkeypatch.setattr(apply_module, "apply_with_atomic_schema", fake_apply_with_atomic_schema)

        called_apply = []
        monkeypatch.setattr(apply_module, "apply", lambda **kwargs: called_apply.append(kwargs))

        tm1_service = object()
        ok, changes = apply_auto(
            changeset,
            tm1_service=tm1_service,
            status_dir="status",
            execution_id="exec-1",
            fail_fast=False,
        )

        assert ok is True
        assert changes == ["ok"]
        assert called_apply == []
        assert captured["changeset"] is changeset
        assert captured["tm1_service"] is tm1_service
        assert captured["status_dir"] == "status"
        assert captured["execution_id"] == "exec-1"
        assert captured["fail_fast"] is False
        assert captured["process"] == decision.estimate.process

    def test_simple_decision_calls_apply_with_whole_changeset(self, monkeypatch):
        changeset = _changeset_with_cube()
        decision = _fake_decision("simple")
        monkeypatch.setattr(apply_module, "choose_apply_strategy", lambda *a, **k: decision)

        called_atomic = []
        monkeypatch.setattr(
            apply_module, "apply_with_atomic_schema", lambda **kwargs: called_atomic.append(kwargs)
        )

        captured = {}

        def fake_apply(**kwargs):
            captured.update(kwargs)
            return False, ["partial"]

        monkeypatch.setattr(apply_module, "apply", fake_apply)

        tm1_service = object()
        ok, changes = apply_auto(changeset, tm1_service=tm1_service, fail_fast=True)

        assert ok is False
        assert changes == ["partial"]
        assert called_atomic == []
        assert captured["changeset"] is changeset
        assert captured["tm1_service"] is tm1_service
        assert captured["fail_fast"] is True

    def test_decision_is_made_on_schema_only_subset_of_changeset(self, monkeypatch):
        # apply_auto must size the decision against the schema-eligible
        # subset (what apply_with_atomic_schema actually sends as the master
        # TI), not the whole changeset, which may include process/chore
        # changes that never enter the master TI.
        cube_change = Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.CUBE,
            uri=Cube.uri_for("Cube_A"),
            body=make_cube(name="Cube_A"),
        )
        process_change = Change(
            change_type=ChangeType.ADD,
            object_type=ObjectType.PROCESS,
            uri="Processes('P1')",
            body=make_process(name="P1"),
        )
        changeset = Changeset()
        changeset.changes = [cube_change, process_change]

        seen_changesets = []

        def fake_choose_apply_strategy(cs, **kwargs):
            seen_changesets.append(cs)
            return _fake_decision("atomic")

        monkeypatch.setattr(apply_module, "choose_apply_strategy", fake_choose_apply_strategy)
        monkeypatch.setattr(apply_module, "apply_with_atomic_schema", lambda **kwargs: (True, None))

        apply_auto(changeset, tm1_service=object())

        assert len(seen_changesets) == 1
        object_types = {c.object_type for c in seen_changesets[0].changes}
        assert object_types == {ObjectType.CUBE}


class TestChangesetApplyAutoWrapper:
    def test_wrapper_delegates_to_services_apply_apply_auto_with_defaults(self, monkeypatch):
        changeset = _changeset_with_cube()
        captured = {}

        def fake_apply_auto(**kwargs):
            captured.update(kwargs)
            return True, ["ok"]

        monkeypatch.setattr(apply_module, "apply_auto", fake_apply_auto)

        tm1_service = object()
        ok, changes = changeset.apply_auto(tm1_service)

        assert ok is True
        assert changes == ["ok"]
        assert captured["changeset"] is changeset
        assert captured["tm1_service"] is tm1_service
        assert captured["max_body_bytes"] == apply_module.DEFAULT_MAX_ATOMIC_BODY_BYTES
        assert captured["max_ti_lines"] == apply_module.DEFAULT_MAX_TI_LINES

    def test_wrapper_forwards_explicit_threshold_overrides(self, monkeypatch):
        changeset = _changeset_with_cube()
        captured = {}

        def fake_apply_auto(**kwargs):
            captured.update(kwargs)
            return True, None

        monkeypatch.setattr(apply_module, "apply_auto", fake_apply_auto)

        changeset.apply_auto(object(), max_body_bytes=1024, max_ti_lines=500)

        assert captured["max_body_bytes"] == 1024
        assert captured["max_ti_lines"] == 500

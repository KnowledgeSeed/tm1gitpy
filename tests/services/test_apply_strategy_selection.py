from tests.unit_common import *

from tm1_git_py.services.apply import (
    ApplyStrategyDecision,
    choose_apply_strategy,
    estimate_atomic_apply_size,
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


class TestChooseApplyStrategy:
    def test_well_under_both_thresholds_chooses_atomic(self):
        changeset = _changeset_with_cube()

        decision = choose_apply_strategy(changeset, max_body_bytes=1_000_000, max_ti_lines=1_000_000)

        assert isinstance(decision, ApplyStrategyDecision)
        assert decision.strategy == "atomic"

    def test_well_over_both_thresholds_chooses_simple(self):
        changeset = _changeset_with_cube()

        decision = choose_apply_strategy(changeset, max_body_bytes=1, max_ti_lines=1)

        assert decision.strategy == "simple"

    def test_byte_only_trip_chooses_simple(self):
        changeset = _changeset_with_cube()
        baseline = estimate_atomic_apply_size(changeset)

        decision = choose_apply_strategy(
            changeset,
            max_body_bytes=baseline.body_bytes - 1,
            max_ti_lines=baseline.ti_line_count,
        )

        assert decision.strategy == "simple"

    def test_line_only_trip_chooses_simple(self):
        changeset = _changeset_with_cube()
        baseline = estimate_atomic_apply_size(changeset)

        decision = choose_apply_strategy(
            changeset,
            max_body_bytes=baseline.body_bytes,
            max_ti_lines=baseline.ti_line_count - 1,
        )

        assert decision.strategy == "simple"

    def test_exact_threshold_boundary_is_not_exceeded_chooses_atomic(self):
        # body_bytes/ti_line_count equal to (not greater than) the threshold
        # must NOT trip the fallback.
        changeset = _changeset_with_cube()
        baseline = estimate_atomic_apply_size(changeset)

        decision = choose_apply_strategy(
            changeset,
            max_body_bytes=baseline.body_bytes,
            max_ti_lines=baseline.ti_line_count,
        )

        assert decision.strategy == "atomic"

    def test_decision_carries_the_thresholds_it_was_called_with(self):
        changeset = _changeset_with_cube()

        decision = choose_apply_strategy(changeset, max_body_bytes=12345, max_ti_lines=6789)

        assert decision.max_body_bytes == 12345
        assert decision.max_ti_lines == 6789

    def test_decision_estimate_matches_a_fresh_estimate(self):
        changeset = _changeset_with_cube()

        decision = choose_apply_strategy(changeset)
        fresh = estimate_atomic_apply_size(changeset)

        assert decision.estimate.body_bytes == fresh.body_bytes
        assert decision.estimate.ti_line_count == fresh.ti_line_count

    def test_empty_changeset_chooses_atomic_by_default(self):
        changeset = Changeset()

        decision = choose_apply_strategy(changeset)

        assert decision.strategy == "atomic"

from tests.unit_common import *

from tm1_git_py.services.apply import (
    build_master_changeset_ti,
    build_master_process,
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


class TestEstimateAtomicApplySize:
    def test_empty_changeset_has_zero_size(self):
        changeset = Changeset()
        estimate = estimate_atomic_apply_size(changeset)

        assert estimate.body_bytes > 0  # still wraps the fixed preamble/comment lines
        assert estimate.ti_line_count == len(estimate.process.prolog_procedure.splitlines())
        assert estimate.master_ti_code == build_master_changeset_ti(changeset)

    def test_body_bytes_matches_process_body_encoded_length(self):
        changeset = _changeset_with_cube()
        estimate = estimate_atomic_apply_size(changeset)

        assert estimate.body_bytes == len(estimate.process.body.encode("utf-8"))

    def test_ti_line_count_matches_wrapped_prolog_procedure_line_splits(self):
        # TM1py.Process.prolog_procedure always prepends a 2-line "Generated
        # Statements" marker, so the estimate must count against the wrapped
        # value TM1 actually receives, not the raw pre-wrap master TI text.
        changeset = _changeset_with_cube()
        estimate = estimate_atomic_apply_size(changeset)

        assert estimate.ti_line_count == len(estimate.process.prolog_procedure.splitlines())
        assert estimate.ti_line_count == len(estimate.master_ti_code.splitlines()) + 2
        assert estimate.master_ti_code == build_master_changeset_ti(changeset)

    def test_larger_changeset_produces_larger_estimate(self):
        small = _changeset_with_cube(name="Cube_Small")

        large_changeset = Changeset()
        large_changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.CUBE,
                uri=Cube.uri_for(f"Cube_{i}"),
                body=make_cube(name=f"Cube_{i}"),
            )
            for i in range(20)
        ]

        small_estimate = estimate_atomic_apply_size(small)
        large_estimate = estimate_atomic_apply_size(large_changeset)

        assert large_estimate.body_bytes > small_estimate.body_bytes
        assert large_estimate.ti_line_count > small_estimate.ti_line_count

    def test_crlf_join_roughly_doubles_raw_ti_length_in_json_body(self):
        # build_master_changeset_ti joins lines with "\r\n"; json.dumps escapes
        # each "\r" and "\n" byte to a 2-character escape sequence, so the
        # serialized body should be noticeably larger than the raw TI text,
        # not merely equal to it.
        changeset = _changeset_with_cube()
        estimate = estimate_atomic_apply_size(changeset)
        raw_ti_bytes = len(estimate.master_ti_code.encode("utf-8"))

        assert estimate.body_bytes > raw_ti_bytes

    def test_estimate_process_name_is_unique_per_call(self):
        changeset = _changeset_with_cube()
        first = estimate_atomic_apply_size(changeset)
        second = estimate_atomic_apply_size(changeset)

        assert first.process.name != second.process.name


class TestBuildMasterProcess:
    def test_build_master_process_reuses_supplied_ti_code(self):
        changeset = _changeset_with_cube()
        master_ti_code = build_master_changeset_ti(changeset)

        process = build_master_process(changeset, master_ti_code=master_ti_code)

        assert process.prolog_procedure.endswith(master_ti_code)

    def test_build_master_process_builds_ti_code_when_not_supplied(self):
        changeset = _changeset_with_cube()

        process = build_master_process(changeset)

        assert process.prolog_procedure.endswith(build_master_changeset_ti(changeset))

    def test_build_master_process_name_has_git_atomic_prefix(self):
        changeset = _changeset_with_cube()
        process = build_master_process(changeset)

        assert process.name.startswith("}git_atomic_")

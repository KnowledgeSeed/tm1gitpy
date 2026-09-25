import logging
import uuid

import pytest
from TM1py import TM1Service

from test_integration.test_base import tm1_service
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.rule import Rule
from tm1_git_py.services.changeset import Change, ChangeType, Changeset, ObjectType

logger = logging.getLogger(__name__)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.usefixtures("tm1_service")
class TestApplyAutoIntegration:
    """
    Exercises `Changeset.apply_auto` end to end against a live TM1 server,
    covering both branches of `choose_apply_strategy`'s decision:
    - a small changeset that comfortably fits under the default thresholds
      (must still route through the atomic master-TI path, preserving
      today's atomicity guarantee for schema changes);
    - a changeset whose atomic-path JSON body exceeds the default 32 KB
      `HTTPRequestEntityMaxSizeInKB` threshold (must fall back to the
      regular per-object `apply()` flow instead of being rejected by TM1
      as an oversized `Process` create call).

    The chosen strategy is asserted from the `apply_auto strategy=...` INFO
    log line emitted by `services.apply.apply_auto`, not inferred indirectly,
    so a regression in the decision itself (not just in whether changes land)
    would fail these tests.
    """

    @pytest.fixture(autouse=True)
    def _tm1_service(self, tm1_service):
        self.tm1_service: TM1Service = tm1_service

    def test_small_changeset_applies_through_atomic_path(self, caplog):
        dim1_name = _uid("ApplyAuto_Small_Dim1")
        dim2_name = _uid("ApplyAuto_Small_Dim2")
        cube_name = _uid("ApplyAuto_Small_Cube")

        dim1_obj = Dimension(name=dim1_name, hierarchies=[], defaultHierarchy=None)
        dim2_obj = Dimension(name=dim2_name, hierarchies=[], defaultHierarchy=None)
        cube_obj = Cube(name=cube_name, dimensions=[dim1_name, dim2_name], rules=[], views=[])

        changeset = Changeset("apply_auto_small_case")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.DIMENSION, Dimension.uri_for(dim1_name), dim1_obj),
            Change(ChangeType.ADD, ObjectType.DIMENSION, Dimension.uri_for(dim2_name), dim2_obj),
            Change(ChangeType.ADD, ObjectType.CUBE, Cube.uri_for(cube_name), cube_obj),
        ]

        try:
            with caplog.at_level(logging.INFO, logger="tm1_git_py.services.apply"):
                ok, _errors = changeset.apply_auto(tm1_service=self.tm1_service)

            assert ok
            assert "apply_auto strategy=atomic" in caplog.text, caplog.text
            assert self.tm1_service.dimensions.exists(dim1_name)
            assert self.tm1_service.dimensions.exists(dim2_name)
            assert self.tm1_service.cubes.exists(cube_name)
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)
            if self.tm1_service.dimensions.exists(dim1_name):
                self.tm1_service.dimensions.delete(dim1_name)
            if self.tm1_service.dimensions.exists(dim2_name):
                self.tm1_service.dimensions.delete(dim2_name)

    def test_oversized_changeset_falls_back_to_simple_path(self, caplog):
        dim1_name = _uid("ApplyAuto_Big_Dim1")
        dim2_name = _uid("ApplyAuto_Big_Dim2")
        cube_name = _uid("ApplyAuto_Big_Cube")

        dim1_obj = Dimension(name=dim1_name, hierarchies=[], defaultHierarchy=None)
        dim2_obj = Dimension(name=dim2_name, hierarchies=[], defaultHierarchy=None)
        cube_obj = Cube(name=cube_name, dimensions=[dim1_name, dim2_name], rules=[], views=[])
        # A single oversized rule statement is enough to push the atomic-path
        # JSON body well past the default 32 KB HTTPRequestEntityMaxSizeInKB
        # threshold (verified at ~118 KB for this padding size).
        huge_statement = "\r\n".join(
            f"# padding line {i} padding padding padding" for i in range(2000)
        )
        rule_obj = Rule(name="default", area="[default]", full_statement=huge_statement)

        changeset = Changeset("apply_auto_oversized_case")
        changeset.changes = [
            Change(ChangeType.ADD, ObjectType.DIMENSION, Dimension.uri_for(dim1_name), dim1_obj),
            Change(ChangeType.ADD, ObjectType.DIMENSION, Dimension.uri_for(dim2_name), dim2_obj),
            Change(ChangeType.ADD, ObjectType.CUBE, Cube.uri_for(cube_name), cube_obj),
            Change(ChangeType.MODIFY, ObjectType.RULE, Rule.uri_for(cube_name), rule_obj),
        ]

        try:
            with caplog.at_level(logging.INFO, logger="tm1_git_py.services.apply"):
                ok, _errors = changeset.apply_auto(tm1_service=self.tm1_service)

            assert ok
            assert "apply_auto strategy=simple" in caplog.text, caplog.text
            assert self.tm1_service.dimensions.exists(dim1_name)
            assert self.tm1_service.dimensions.exists(dim2_name)
            assert self.tm1_service.cubes.exists(cube_name)
            cube_after = self.tm1_service.cubes.get(cube_name)
            assert cube_after.rules is not None
            assert "padding line 0" in str(cube_after.rules)
        finally:
            if self.tm1_service.cubes.exists(cube_name):
                self.tm1_service.cubes.delete(cube_name)
            if self.tm1_service.dimensions.exists(dim1_name):
                self.tm1_service.dimensions.delete(dim1_name)
            if self.tm1_service.dimensions.exists(dim2_name):
                self.tm1_service.dimensions.delete(dim2_name)

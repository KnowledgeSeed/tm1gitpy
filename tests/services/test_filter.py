from tests.unit_common import *


class TestTechnicalObjectFilters:

    def test_ignore_technical_objects_defaults_when_rules_missing(self):
        assert with_technical_objects_ignore(None) == DEFAULT_TM1_TECHNICAL_OBJECTS

    def test_ignore_technical_objects_extends_custom_rules(self):
        filter_rules = ["Dimensions('Sales*')"]

        assert with_technical_objects_ignore(filter_rules) == [
            "Dimensions('Sales*')",
            *DEFAULT_TM1_TECHNICAL_OBJECTS,
        ]

    def test_ignore_technical_objects_preserves_force_include_precedence(self):
        effective_rules = with_technical_objects_ignore(["!Cubes('}*')"])

        assert "!Cubes('}*')" in effective_rules
        assert "Cubes('}*')" in effective_rules
        assert not should_exclude_path("Cubes('}Stats')", effective_rules)

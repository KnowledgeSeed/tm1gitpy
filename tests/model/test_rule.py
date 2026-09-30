from tests.unit_common import *
from tm1_git_py.model.rule import RuleRegionError, parse_rules, render_rules


def _names(rules):
    return [rule.name for rule in rules]


def _texts(rules):
    return [rule.full_statement for rule in rules]


class TestParseRules:

    def test_empty_text_has_no_rules(self):
        assert parse_rules("", "Sales") == []

    def test_text_without_regions_is_one_default_rule(self):
        text = "SKIPCHECK;\n['a']=N:1;\nFEEDERS;\n"

        rules = parse_rules(text, "Sales")

        assert _names(rules) == ["default"]
        assert rules[0].full_statement == text
        assert rules[0].uri("Sales") == "Cubes('Sales')/Rules('default')"

    def test_single_unlabeled_region(self):
        text = "#region\n['a']=N:1;\n#endregion\n"

        rules = parse_rules(text, "Sales")

        assert _names(rules) == ["region_0001"]
        assert _texts(rules) == [text]

    def test_multiple_regions_with_preamble_gaps_and_trailer(self):
        text = (
            "#SEEDER;\nSKIPCHECK;\n"
            "#region Sales\n['a']=N:1;\n#endregion\n"
            "\n# between\n"
            "#region\n['b']=N:1;\n#endregion\n"
            "FEEDERS;\n['a']=>['b'];\n"
        )

        rules = parse_rules(text, "Sales")

        assert _names(rules) == ["preamble", "region_0001", "region_0002", "trailer"]
        assert _texts(rules) == [
            "#SEEDER;\nSKIPCHECK;\n",
            "#region Sales\n['a']=N:1;\n#endregion\n",
            "\n# between\n#region\n['b']=N:1;\n#endregion\n",
            "FEEDERS;\n['a']=>['b'];\n",
        ]

    def test_labels_do_not_affect_names_and_may_repeat(self):
        text = (
            "#region Same\n['a']=N:1;\n#endregion\n"
            "#region Same\n['b']=N:1;\n#endregion\n"
            "#region\n['c']=N:1;\n#endregion\n"
        )

        assert _names(parse_rules(text, "Sales")) == ["region_0001", "region_0002", "region_0003"]

    def test_comment_and_blank_lines_after_region_start_stay_in_region(self):
        text = "#region A\n\n# note\n['a']=N:1;\n#endregion\n"

        rules = parse_rules(text, "Sales")

        assert _texts(rules) == [text]

    def test_markers_are_case_insensitive_and_may_be_indented(self):
        text = "  #Region A\n['a']=N:1;\n\t#ENDREGION\n"

        assert _names(parse_rules(text, "Sales")) == ["region_0001"]

    @pytest.mark.parametrize(
        "comment",
        ["#regional totals", "#Regions below are generated", "#endregions", "#region_old", "#endregion-x"],
    )
    def test_comment_starting_with_marker_text_is_not_a_marker(self, comment):
        text = f"SKIPCHECK;\n{comment}\n['a']=N:1;\n"

        rules = parse_rules(text, "Sales")

        assert _names(rules) == ["default"]
        assert rules[0].full_statement == text

    def test_comment_starting_with_marker_text_stays_inside_region(self):
        text = "#region A\n#regional totals\n['a']=N:1;\n#endregions end here\n#endregion A\n"

        rules = parse_rules(text, "Sales")

        assert _names(rules) == ["region_0001"]
        assert _texts(rules) == [text]

    @pytest.mark.parametrize(
        "text",
        [
            "SKIPCHECK;\r\n#region A\r\n['a']=N:1;\r\n#endregion\r\nFEEDERS;\r\n",
            "SKIPCHECK;\n#region A\n['a']=N:1;\n#endregion\nFEEDERS;",
            "#region A\r\n['a']=N:1;\r\n#endregion",
            "SKIPCHECK;\r\n['a']=N:1;",
        ],
        ids=["crlf", "lf-no-final-newline", "crlf-no-final-newline", "no-regions"],
    )
    def test_round_trip_is_exact(self, text):
        rules = parse_rules(text, "Sales")

        assert Cube(name="Sales", dimensions=[], rules=rules, views=[]).get_rule_text() == text
        assert render_rules(rules) == text

    def test_rule_expression_syntax_is_kept_verbatim(self):
        # Rule 14: names with '@' are quoted in rules, and a single quote in a name is doubled
        text = (
            "#region\n"
            "['products@location'] = N: DB('Sales@EU', !Version, 'O''Brien', !Period);\n"
            "#endregion\n"
            "['Rock''n''Roll'] = S: IF(!Store @= 'Main', 'x', 'y');\n"
        )

        rules = parse_rules(text, "O'Brien@Sales")

        assert _names(rules) == ["region_0001", "trailer"]
        assert render_rules(rules) == text
        assert rules[0].full_statement == (
            "#region\n"
            "['products@location'] = N: DB('Sales@EU', !Version, 'O''Brien', !Period);\n"
            "#endregion\n"
        )


class TestParseRulesDiagnostics:

    def test_unclosed_region_reports_its_start_line(self):
        text = "SKIPCHECK;\n#region A\n['a']=N:1;\n"

        with pytest.raises(RuleRegionError) as exc_info:
            parse_rules(text, "Sales", source="cubes/Sales.rules")

        assert exc_info.value.line_number == 2
        assert "cubes/Sales.rules:2" in str(exc_info.value)
        assert "is not closed" in str(exc_info.value)

    def test_nested_region_reports_both_lines(self):
        text = "#region A\n['a']=N:1;\n#region B\n#endregion\n#endregion\n"

        with pytest.raises(RuleRegionError) as exc_info:
            parse_rules(text, "Sales")

        assert exc_info.value.line_number == 3
        assert "line 3" in str(exc_info.value)
        assert "opened on line 1" in str(exc_info.value)

    def test_unmatched_end_marker(self):
        text = "SKIPCHECK;\n#endregion\n"

        with pytest.raises(RuleRegionError) as exc_info:
            parse_rules(text, "Sales")

        assert exc_info.value.line_number == 2
        assert exc_info.value.cube_name == "Sales"
        assert "without an open region" in str(exc_info.value)


class TestRuleUris:

    def test_uri_uses_the_positional_name(self):
        rules = parse_rules("#region\n#endregion\n", "Sales")

        assert rules[0].uri("Sales") == "Cubes('Sales')/Rules('region_0001')"
        assert Rule.uri_for("Sales", "trailer") == "Cubes('Sales')/Rules('trailer')"
        assert Rule.uri_for("Sales") == "Cubes('Sales')/Rules('default')"

    def test_cube_name_from_region_uri(self):
        assert Rule.cube_name_from_uri("Cubes('Sales')/Rules('region_0002')") == "Sales"
        assert Rule.cube_name_from_uri("Cubes('O''Brien')/Rules('preamble')") == "O'Brien"
        assert Rule.cube_name_from_uri("Cubes('Sales')/DrillthroughRules('default')") == ""

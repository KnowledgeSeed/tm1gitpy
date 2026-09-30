import json
import logging
import re
from typing import Dict, Any, Iterable, List, Optional, Tuple

from TM1py import TM1Service
from requests import Response

logger = logging.getLogger(__name__)

REGION_START_MARKER = "#region"
REGION_END_MARKER = "#endregion"
PREAMBLE_RULE_NAME = "preamble"
TRAILER_RULE_NAME = "trailer"
DEFAULT_RULE_NAME = "default"

# a marker is the whole first word of a line, so comments such as "#regional totals" are not markers
_REGION_START_PATTERN = re.compile(rf"^{re.escape(REGION_START_MARKER)}(?:\s|$)", re.IGNORECASE)
_REGION_END_PATTERN = re.compile(rf"^{re.escape(REGION_END_MARKER)}(?:\s|$)", re.IGNORECASE)


class RuleRegionError(ValueError):
    def __init__(self, message: str, cube_name: str, source: str, line_number: int):
        location = f"{source}:{line_number}" if source else f"line {line_number}"
        super().__init__(f"Invalid rule regions in cube '{cube_name}' ({location}): {message}")
        self.cube_name = cube_name
        self.source = source
        self.line_number = line_number


class Rule:
    def __init__(
            self,
            area: str,
            full_statement: str,
            comment: str = "",
            *,
            name: Optional[str] = None,
    ):
        self.area = area
        self.full_statement = full_statement
        self.comment = comment
        self.name = name or "default"
        self._normalized_statement = "".join(full_statement.split())
        self._normalized_comment = "".join(comment.split())

    def __eq__(self, other):
        if not isinstance(other, Rule):
            return NotImplemented
        return self.name == other.name and \
               self.area == other.area and \
               self._normalized_statement == other._normalized_statement and \
               self._normalized_comment == other._normalized_comment

    def __hash__(self):
        return hash((self.name, self.area, self._normalized_statement, self._normalized_comment))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "area": self.area,
            "full_statement": self.full_statement,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Rule":
        name = data.get("name") or data.get("Name")
        area = data.get("area") or data.get("Area") or ""
        if not name:
            name = cls.name_from_area(area)
        statement = data.get("full_statement") or data.get("fullStatement") or data.get("statement") or data.get("rule") or ""
        comment = data.get("comment") or data.get("Comment") or ""
        if not area:
            area = f"[{name}]"
        return cls(
            area=area,
            full_statement=statement,
            comment=comment,
            name=name,
        )

    @staticmethod
    def name_from_area(area: str) -> str:
        raw = (area or "").strip()
        if raw.startswith("[") and raw.endswith("]") and len(raw) >= 2:
            raw = raw[1:-1]
        raw = raw.replace("''", "'").strip().strip("'").strip('"')
        if not raw:
            return "subrule_default"
        slug = re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_").lower()
        return f"subrule_{slug or 'default'}"

    @staticmethod
    def uri_for(cube_name: str, rule_name: str = "default") -> str:
        return f"Cubes('{cube_name}')/Rules('{rule_name}')"

    def uri(self, cube_name: str) -> str:
        return self.uri_for(cube_name, self.name)

    @staticmethod
    def cube_name_from_uri(uri: str) -> str:
        match = re.match(r"^Cubes\('((?:''|[^'])+)'\)/Rules\('(?:''|[^'])+'\)$", uri or "")
        if not match:
            return ""
        return match.group(1).replace("''", "'")

    @staticmethod
    def drillthrough_cube_name_from_uri(uri: str) -> str:
        match = re.match(r"^Cubes\('((?:''|[^'])+)'\)/DrillthroughRules\('(?:''|[^'])+'\)$", uri or "")
        if not match:
            return ""
        cube_name = match.group(1).replace("''", "'")
        return f"}}CubeDrill_{cube_name}"


def region_rule_name(ordinal: int) -> str:
    return f"region_{ordinal:04d}"


def _segment_rule(name: str, text: str) -> Rule:
    return Rule(area=f"[{name}]", full_statement=text, comment="", name=name)


def parse_rules(rule_text: str, cube_name: str, source: str = "") -> List[Rule]:
    """Split rule text into ordered preamble / region_000N / trailer Rules.

    Concatenating the returned full_statement values reproduces rule_text exactly.
    Text between two regions is attached to the start of the following region.
    Rule text without region markers is returned as a single 'default' Rule.
    """
    if not rule_text:
        return []

    # split after each "\n" so "\r\n" and a final line without newline are kept verbatim
    lines = [line for line in re.split(r"(?<=\n)", rule_text) if line]

    rules: List[Rule] = []
    buffer: List[str] = []
    in_region = False
    region_start_line = 0
    region_count = 0

    for line_number, line in enumerate(lines, start=1):
        marker = line.strip()
        if _REGION_END_PATTERN.match(marker):
            if not in_region:
                raise RuleRegionError(
                    f"'{REGION_END_MARKER}' without an open region",
                    cube_name, source, line_number,
                )
            buffer.append(line)
            rules.append(_segment_rule(region_rule_name(region_count), "".join(buffer)))
            buffer = []
            in_region = False
        elif _REGION_START_PATTERN.match(marker):
            if in_region:
                raise RuleRegionError(
                    f"nested '{REGION_START_MARKER}' (region opened on line {region_start_line} is not closed)",
                    cube_name, source, line_number,
                )
            if region_count == 0 and buffer:
                rules.append(_segment_rule(PREAMBLE_RULE_NAME, "".join(buffer)))
                buffer = []
            buffer.append(line)
            in_region = True
            region_start_line = line_number
            region_count += 1
        else:
            buffer.append(line)

    if in_region:
        raise RuleRegionError(
            f"'{REGION_START_MARKER}' is not closed by '{REGION_END_MARKER}'",
            cube_name, source, region_start_line,
        )

    if region_count == 0:
        return [_segment_rule(DEFAULT_RULE_NAME, rule_text)]

    if buffer:
        rules.append(_segment_rule(TRAILER_RULE_NAME, "".join(buffer)))
    return rules


_REGION_RULE_NAME_PATTERN = re.compile(r"^region_(\d+)$")


def extract_cube_rule_text(cube: Any) -> str:
    """Return the rule text of a TM1py Cube, or "" when it has no rules."""
    if not getattr(cube, "has_rules", False):
        return ""
    raw_body = getattr(getattr(cube, "rules", None), "body", "")
    try:
        rule_data = json.loads(raw_body)
        return rule_data.get("Rules", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return raw_body if isinstance(raw_body, str) else ""


def _rule_segment_sort_key(name: str) -> Tuple[int, int]:
    if name == PREAMBLE_RULE_NAME:
        return 0, 0
    if name == DEFAULT_RULE_NAME:
        return 1, 0
    region_match = _REGION_RULE_NAME_PATTERN.match(name)
    if region_match:
        return 2, int(region_match.group(1))
    if name == TRAILER_RULE_NAME:
        return 3, 0
    raise ValueError(f"Unsupported rule name '{name}'")


def render_rules(rules: Iterable[Rule]) -> str:
    """Concatenate rule segments in preamble / default / region_000N / trailer order."""
    ordered = sorted(rules, key=lambda rule: _rule_segment_sort_key(rule.name))
    return "".join(rule.full_statement for rule in ordered)


def merge_rule_text(target_text: str, cube_name: str, rules: Iterable[Rule]) -> str:
    """Overlay rule segments on a cube's current rule text and render the result.

    *target_text* is split with ``parse_rules``. Each rule in *rules* replaces
    the target segment with the same name, or adds it; a rule with an empty
    ``full_statement`` removes that segment. Segments not named in *rules* keep
    the target text verbatim. Raises ``ValueError`` for an unknown rule name or
    when the result would mix the unmarked ``default`` rule with region rules.
    """
    segments = {
        rule.name: rule
        for rule in parse_rules(target_text, cube_name, source="TM1 server")
    }

    for rule in rules:
        if not rule.full_statement:
            if segments.pop(rule.name, None) is None:
                logger.warning(
                    "Rule '%s' to remove not found in cube '%s' on the server; nothing to remove",
                    rule.name, cube_name,
                )
            continue
        segments[rule.name] = rule

    if DEFAULT_RULE_NAME in segments and len(segments) > 1:
        raise ValueError(
            f"Cannot apply rule regions to cube '{cube_name}': the result would mix the "
            f"unmarked '{DEFAULT_RULE_NAME}' rule with region rules "
            f"({', '.join(sorted(name for name in segments if name != DEFAULT_RULE_NAME))}). "
            f"Select the '{DEFAULT_RULE_NAME}' rule change together with the region changes."
        )

    return render_rules(segments.values())


def _target_rule_cube_name(uri: Optional[str]) -> str:
    uri_text = uri or ""
    drillthrough_cube_name = Rule.drillthrough_cube_name_from_uri(uri_text)
    if drillthrough_cube_name:
        return drillthrough_cube_name
    return Rule.cube_name_from_uri(uri_text)


def create_rule(tm1_service: TM1Service, rule: Rule, uri: Optional[str] = None) -> Response:
    cube_name = _target_rule_cube_name(uri)
    return tm1_service.cubes.update_or_create_rules(cube_name=cube_name, rules=rule.full_statement)


def update_rule(tm1_service: TM1Service, rule: Rule, uri: Optional[str] = None) -> Response:
    cube_name = _target_rule_cube_name(uri)
    return tm1_service.cubes.update_or_create_rules(cube_name=cube_name, rules=rule.full_statement)


def delete_rule(tm1_service: TM1Service, rule: Rule, uri: Optional[str] = None) -> Response:
    cube_name = _target_rule_cube_name(uri)
    return tm1_service.cubes.update_or_create_rules(cube_name=cube_name, rules="")

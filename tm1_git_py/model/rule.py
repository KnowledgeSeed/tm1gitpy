import re
from typing import Dict, Any, Optional

from TM1py import TM1Service
from requests import Response


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
    def uri_for(cube_name: str) -> str:
        return f"Cubes('{cube_name}')/Rules('default')"

    def uri(self, cube_name: str) -> str:
        return self.uri_for(cube_name)

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


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).replace("'", "''")


def format_rul_for_ti(rule_text: str | None) -> str:
    if rule_text is None:
        return "''"
    lines = str(rule_text).splitlines()
    if not lines:
        return "''"
    return " | CHAR(10) | ".join(f"'{_escape_ti(line)}'" for line in lines)


def build_rule_create_ti(rule: Rule, uri: Optional[str] = None) -> str:
    cube_name = _target_rule_cube_name(uri)
    cube_clean = _escape_ti(cube_name)
    rule_expr = format_rul_for_ti(rule.full_statement)
    lines = [
        f"# --- Create Cube Rules: {cube_clean} ---",
        f"IF( CubeExists('{cube_clean}') = 1 );",
        f"    CubeRuleSet('{cube_clean}', {rule_expr});",
        "ENDIF;"
    ]
    return "\r\n".join(lines)


def build_rule_update_ti(rule: Rule, uri: Optional[str] = None) -> str:
    cube_name = _target_rule_cube_name(uri)
    cube_clean = _escape_ti(cube_name)
    rule_expr = format_rul_for_ti(rule.full_statement)
    lines = [
        f"# --- Update Cube Rules: {cube_clean} ---",
        f"IF( CubeExists('{cube_clean}') = 1 );",
        f"    CubeRuleSet('{cube_clean}', {rule_expr});",
        "ENDIF;"
    ]
    return "\r\n".join(lines)


def build_rule_delete_ti(rule: Rule, uri: Optional[str] = None) -> str:
    _ = rule
    cube_name = _target_rule_cube_name(uri)
    cube_clean = _escape_ti(cube_name)
    lines = [
        f"# --- Delete Cube Rules: {cube_clean} ---",
        f"IF( CubeExists('{cube_clean}') = 1 );",
        f"    CubeRuleDestroy('{cube_clean}');",
        "ENDIF;"
    ]
    return "\r\n".join(lines)

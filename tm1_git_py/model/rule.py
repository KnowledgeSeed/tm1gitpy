import re
from typing import Dict, Any, Optional


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

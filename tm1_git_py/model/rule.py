from typing import Dict, Any, Optional


class Rule:
    def __init__(
            self,
            area: str,
            full_statement: str,
            comment: str = "",
            *,
            source_path: Optional[str] = None,
            cube_name: Optional[str] = None
    ):
        self.area = area
        self.full_statement = full_statement
        self.comment = comment
        self.source_path = source_path or (self.as_link(cube_name) if cube_name else None)
        self._normalized_statement = "".join(full_statement.split())
        self._normalized_comment = "".join(comment.split())

    def __eq__(self, other):
        if not isinstance(other, Rule):
            return NotImplemented
        return self.area == other.area and \
               self._normalized_statement == other._normalized_statement and \
               self._normalized_comment == other._normalized_comment

    def __hash__(self):
        return hash((self.area, self._normalized_statement, self._normalized_comment))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "area": self.area,
            "full_statement": self.full_statement,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any],
            *,
            source_path: Optional[str] = None,
            cube_name: Optional[str] = None
    ) -> "Rule":
        area = data.get("area") or data.get("Area") or ""
        statement = data.get("full_statement") or data.get("fullStatement") or data.get("statement") or data.get("rule") or ""
        comment = data.get("comment") or data.get("Comment") or ""
        resolved_path = source_path or (f"cubes/{cube_name}.rules" if cube_name else None)
        return cls(area=area, full_statement=statement, comment=comment, source_path=resolved_path)

    @staticmethod
    def as_link(cube_base_name):
        # /dimensions/Dimension_A.hierarchies/Dimension_A.json/element1
        return '/cubes/' + cube_base_name + '.rules'

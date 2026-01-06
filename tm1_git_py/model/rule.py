from typing import Dict, Any


class Rule:
    def __init__(self, area: str, full_statement: str, comment: str = ""):
        self.area = area
        self.full_statement = full_statement
        self.comment = comment
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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Rule":
        area = data.get("area") or data.get("Area") or ""
        statement = data.get("full_statement") or data.get("fullStatement") or data.get("statement") or ""
        comment = data.get("comment") or data.get("Comment") or ""
        return cls(area=area, full_statement=statement, comment=comment)
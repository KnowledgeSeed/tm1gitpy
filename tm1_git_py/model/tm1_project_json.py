"""TM1 project file (tm1project.json) representation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

_TM1PROJECT_FILENAME = "tm1project.json"


@dataclass(frozen=True)
class Tm1ProjectJson:
    """Parsed tm1project.json (ignore section used for filter conversion)."""

    version: Any
    ignore: tuple[str, ...]

    @classmethod
    def from_path(cls, path: str | Path) -> "Tm1ProjectJson":
        project_path = Path(path).expanduser().resolve()
        with open(project_path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"tm1project file must contain a JSON object: {project_path}")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tm1ProjectJson":
        if "Version" not in data:
            raise ValueError("tm1project.json must contain a Version property")
        ignore_raw = data.get("Ignore", [])
        if ignore_raw is None:
            ignore_raw = []
        if not isinstance(ignore_raw, list):
            raise ValueError("Ignore must be a list when present")
        ignore_rules = tuple(
            str(entry).strip()
            for entry in ignore_raw
            if str(entry).strip()
        )
        return cls(version=data["Version"], ignore=ignore_rules)

    @classmethod
    def is_tm1project_path(cls, path: str | Path) -> bool:
        """Return True when *path* looks like a tm1project.json file."""
        project_path = Path(path).expanduser()
        if project_path.name.lower() == _TM1PROJECT_FILENAME:
            return True
        try:
            with open(project_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and "Version" in data

    def ignore_rules(self) -> List[str]:
        """Return raw ignore entries as filter rule lines."""
        return list(self.ignore)

    def to_filter_rules(self):
        """Build effective :class:`~tm1_git_py.services.filter.FilterRules` from Ignore."""
        from tm1_git_py.services.filter import FilterRules, apply_default_filter_rules

        return apply_default_filter_rules(FilterRules(self.ignore_rules()))

import fnmatch
import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterator, List, Mapping, Optional

from tm1_git_py.changeset import Change, Changeset, ChangeType, normalize_reference_path
from tm1_git_py.model import Model
from tm1_git_py.model.store_backed_sequence import StoreBackedSequence

class EntityType(str, Enum):
    DIMENSION = "dimension"
    HIERARCHY = "hierarchy"
    ELEMENT = "element"
    SUBSET = "subset"
    EDGE = "edge"
    CUBE = "cube"
    VIEW = "view"
    RULE = "rule"
    PROCESS = "process"
    CHORE = "chore"
    TASK = "task"


_ENTITY_RULE_PATTERNS: dict[EntityType, str] = {
    EntityType.DIMENSION: r"^Dimensions\('([^']*)'\)$",
    EntityType.HIERARCHY: r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)$",
    EntityType.ELEMENT: r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Elements\('([^']*)'\)$",
    EntityType.SUBSET: r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Subsets\('([^']*)'\)$",
    # Edge format (preferred): Edges('parentName'/'componentName')
    # Backward compatible: Edges('parentName/componentName') or Edges('*')
    EntityType.EDGE: (
        r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Edges\("
        r"(?:'([^']*)'/'([^']*)'|'([^'/]*)/([^']*)'|'([^']*)')"
        r"\)$"
    ),
    EntityType.CUBE: r"^Cubes\('([^']*)'\)$",
    EntityType.VIEW: r"^Cubes\('([^']*)'\)/Views\('([^']*)'\)$",
    EntityType.RULE: r"^Cubes\('([^']*)'\)/Rules\('([^']*)'\)(?:\|.*)?$",
    EntityType.PROCESS: r"^Processes\('([^']*)'\)$",
    EntityType.CHORE: r"^Chores\('([^']*)'\)$",
    EntityType.TASK: r"^Chores\('([^']*)'\)/Tasks\('([^']*)'\)$",
}


@dataclass
class Tm1FilterResult:
    """Result of building a TM1 OData filter, with optional skip_all hint."""

    filter_expr: Optional[str]
    """The OData $filter expression, or None if no filtering applies."""

    skip_all: bool
    """If True, the filter would exclude everything; exporter can skip TM1 calls."""

    applicable_rules: List[str]
    """Normalized filter rules that were applicable to this TM1 filter scope."""


def _chain_matches(
    ancestor_chain: list[tuple[EntityType, str]],
    chain: list[tuple[EntityType, str]],
) -> bool:
    """Return True if ancestor_chain patterns match the concrete parent chain."""
    for (ancestor_type, ancestor_pattern), (parent_type, parent_name_val) in zip(
        ancestor_chain, chain
    ):
        if ancestor_type != parent_type:
            return False
        if not _identifier_pattern_matches(parent_name_val, ancestor_pattern):
            return False
    return True


def _is_match_all_identifier(pattern: str) -> bool:
    """Return True if identifier pattern matches everything (e.g. '*' or '*/*' for edges)."""
    if not pattern:
        return False
    if pattern == "*":
        return True
    if "/" in pattern:
        return all(_is_match_all_identifier(p.strip()) for p in pattern.split("/"))
    return False


@dataclass
class FilterRules:
    """Pre-normalized filter rules for path exclusion checks."""

    _normalized_rules: List[str]
    _parsed_rules: List[dict[str, Any]]

    def __init__(self, filter_rules: List[str], *, raise_on_invalid_rule: bool = False):
        normalized: List[str] = []
        for rule in filter_rules or []:
            if not rule:
                continue
            if not self._validate_url_rule_pattern(rule):
                continue
            pattern = rule[1:].lstrip("/") if rule.startswith("!") else rule
            if not _rule_matches_any_entity_pattern(pattern):
                if raise_on_invalid_rule:
                    raise ValueError(
                        f"Invalid filter rule: {rule!r} does not match any entity pattern"
                    )
                continue
            normalized.append(rule)
        object.__setattr__(self, "_normalized_rules", normalized)
        parsed_rules: List[dict[str, Any]] = []
        for rule in normalized:
            is_include, pattern = _split_rule_prefix(rule)
            parsed = _parse_selector_pattern(pattern)
            if not parsed:
                continue
            parsed_rules.append(
                {
                    "op": "!" if is_include else "-",
                    "raw_rule": rule,
                    **parsed,
                }
            )
        object.__setattr__(self, "_parsed_rules", parsed_rules)

    @property
    def has_rules(self) -> bool:
        """Return True if this filter has any rules."""
        return bool(self._normalized_rules)

    def should_exclude(self, object_url: str) -> bool:
        """Return True when the object URI is excluded by effective rules.

        Evaluation follows README semantics:
        - no prefix = exclude
        - '!' prefix = force include
        - parent-first traversal: excluded parent blocks descendants
        - per-level effective logic: (not excludes) or includes
        """
        if not self._normalized_rules:
            return False
        context = _parse_object_selector(object_url)
        if not context:
            return False
        force_include_branch = self._is_force_include_related_to_target(context)

        chain = context["ancestor_chain"]
        for idx, (entity_type, name) in enumerate(chain):
            if self._is_excluded_at_level(
                entity_type=entity_type,
                ancestor_chain=chain[:idx],
                identifier=name,
                area=None,
            ):
                if force_include_branch:
                    continue
                return True

        is_excluded = self._is_excluded_at_level(
            entity_type=context["entity_type"],
            ancestor_chain=chain,
            identifier=context["identifier"],
            area=context["area"],
        )
        if is_excluded and force_include_branch:
            return False
        return is_excluded

    def get_winning_rule(self, object_url: str) -> dict[str, str] | None:
        """Return the winning rule for the given object URL, or None."""
        if not self._normalized_rules:
            return None
        context = _parse_object_selector(object_url)
        if not context:
            return None

        matched = [
            r for r in self._parsed_rules
            if _selector_rule_matches_context(r, context)
        ]
        if not matched:
            return None

        include_rules = [r for r in matched if r["op"] == "!"]
        candidate_pool = include_rules or matched
        winner = max(candidate_pool, key=_selector_rule_specificity)
        return {"op": winner["op"], "pattern": winner["pattern"]}

    def _is_excluded_at_level(
        self,
        *,
        entity_type: EntityType,
        ancestor_chain: list[tuple[EntityType, str]],
        identifier: str,
        area: Optional[str],
    ) -> bool:
        context = {
            "entity_type": entity_type,
            "ancestor_chain": ancestor_chain,
            "identifier": identifier,
            "area": area,
        }
        include_match = any(
            r["op"] == "!" and _selector_rule_matches_context(r, context)
            for r in self._parsed_rules
        )
        if include_match:
            return False
        exclude_match = any(
            r["op"] == "-" and _selector_rule_matches_context(r, context)
            for r in self._parsed_rules
        )
        return exclude_match

    def get_rules_for_entity(self, entity: str | EntityType) -> List[str]:
        """Return normalized rules whose URL format matches the given entity type."""
        entity_type = _resolve_entity_type(entity)
        if entity_type is None:
            return []
        matched_rules: List[str] = []
        for rule in self._normalized_rules:
            _is_include, pattern = _split_rule_prefix(rule)
            if _match_entity_pattern(entity_type, pattern):
                matched_rules.append(rule)
        return matched_rules

    def _parsed_rules_for_entity(self, entity_type: EntityType) -> List[dict[str, Any]]:
        matched: List[dict[str, Any]] = []
        for parsed_rule in self._parsed_rules:
            match = _match_entity_pattern(entity_type, parsed_rule["pattern"])
            if match:
                matched.append(parsed_rule)
        return matched

    def _force_include_patterns_for_target(
        self,
        *,
        target_entity_type: EntityType,
        ancestor_chain: list[tuple[EntityType, str]],
    ) -> tuple[list[str], list[str]]:
        """Extract target-level identifier patterns from descendant include rules.

        Example:
        - target=Dimension, chain=[] from
          !Dimensions('Sales')/Hierarchies('Main')/Elements('Leaf*')
          => includes Dimension pattern 'Sales'
        - target=Hierarchy, chain=[(Dimension,'Sales')] from
          !Dimensions('Sales')/Hierarchies('Main')/Elements('Leaf*')
          => includes Hierarchy pattern 'Main'
        """
        identifier_patterns: list[str] = []
        applicable_rules: list[str] = []
        for parsed_rule in self._parsed_rules:
            if parsed_rule.get("op") != "!":
                continue
            rule_path = list(parsed_rule.get("ancestor_chain", [])) + [
                (parsed_rule["entity_type"], parsed_rule["identifier_pattern"])
            ]
            if len(rule_path) <= len(ancestor_chain):
                continue

            prefix_matches = True
            for (ancestor_type, ancestor_name), (rule_type, rule_pattern) in zip(
                ancestor_chain, rule_path
            ):
                if ancestor_type != rule_type:
                    prefix_matches = False
                    break
                if not _identifier_pattern_matches(ancestor_name, rule_pattern):
                    prefix_matches = False
                    break
            if not prefix_matches:
                continue

            target_node = rule_path[len(ancestor_chain)]
            if target_node[0] != target_entity_type:
                continue
            target_pattern = str(target_node[1])
            if target_pattern not in identifier_patterns:
                identifier_patterns.append(target_pattern)
            raw_rule = str(parsed_rule["raw_rule"])
            if raw_rule not in applicable_rules:
                applicable_rules.append(raw_rule)
        return identifier_patterns, applicable_rules

    @staticmethod
    def _context_path(context: dict[str, Any]) -> list[tuple[EntityType, str]]:
        return list(context.get("ancestor_chain", [])) + [
            (context["entity_type"], context["identifier"])
        ]

    @staticmethod
    def _paths_overlap_by_rule_prefix(
        target_path: list[tuple[EntityType, str]],
        rule_path: list[tuple[EntityType, str]],
    ) -> bool:
        """Return True when target and rule share a matching prefix.

        Rule path identifiers are patterns, target path identifiers are concrete.
        """
        overlap_len = min(len(target_path), len(rule_path))
        if overlap_len == 0:
            return False
        for index in range(overlap_len):
            target_type, target_name = target_path[index]
            rule_type, rule_pattern = rule_path[index]
            if target_type != rule_type:
                return False
            if not _identifier_pattern_matches(target_name, rule_pattern):
                return False
        return True

    def _is_force_include_related_to_target(self, target_context: dict[str, Any]) -> bool:
        target_path = self._context_path(target_context)
        for rule in self._parsed_rules:
            if rule.get("op") != "!":
                continue
            rule_path = list(rule.get("ancestor_chain", [])) + [
                (rule["entity_type"], rule["identifier_pattern"])
            ]
            if self._paths_overlap_by_rule_prefix(target_path, rule_path):
                return True
        return False

    def to_tm1_name_filter(
        self, entity_type: EntityType, name_property: str = "Name"
    ) -> Tm1FilterResult:
        """Build a TM1 OData $filter expression from entity-specific name rules."""
        include_predicates: List[str] = []
        exclude_predicates: List[str] = []
        exclude_has_match_all = False

        applicable_rules: List[str] = []
        for parsed_rule in self._parsed_rules_for_entity(entity_type):
            rule = str(parsed_rule["raw_rule"])
            is_include = parsed_rule["op"] == "!"
            pattern = str(parsed_rule["pattern"])
            match = _match_entity_pattern(entity_type, pattern)
            if not match:
                continue

            identifier_pattern = match.groups()[-1]
            predicate = _identifier_pattern_to_tm1_filter(
                identifier_pattern=identifier_pattern,
                name_property=name_property,
            )
            if predicate is None:
                continue
            if is_include:
                include_predicates.append(predicate)
            else:
                exclude_predicates.append(predicate)
                if _is_match_all_identifier(identifier_pattern):
                    exclude_has_match_all = True
            if rule not in applicable_rules:
                applicable_rules.append(rule)

        inherited_patterns, inherited_rules = self._force_include_patterns_for_target(
            target_entity_type=entity_type,
            ancestor_chain=[],
        )
        for identifier_pattern in inherited_patterns:
            predicate = _identifier_pattern_to_tm1_filter(
                identifier_pattern=identifier_pattern,
                name_property=name_property,
            )
            if predicate and predicate not in include_predicates:
                include_predicates.append(predicate)
        for inherited_rule in inherited_rules:
            if inherited_rule not in applicable_rules:
                applicable_rules.append(inherited_rule)

        return _compose_tm1_filter_result(
            include_predicates=include_predicates,
            exclude_predicates=exclude_predicates,
            exclude_has_match_all=exclude_has_match_all,
            applicable_rules=applicable_rules,
        )

    def to_tm1_child_name_filter(
        self,
        *,
        parent_chain: Optional[List[tuple[EntityType, str]]] = None,
        parent_entity_type: Optional[EntityType] = None,
        child_entity_type: Optional[EntityType] = None,
        parent_name: Optional[str] = None,
        name_property: str = "Name",
    ) -> Tm1FilterResult:
        """Build TM1 OData $filter for child names scoped to parent object(s).

        Supports 2-level (one parent) and 3-level (two parents) matching.
        Use parent_chain for the new API, or parent_entity_type+parent_name for backward compat.
        """
        if child_entity_type is None:
            raise TypeError("child_entity_type is required")
        if parent_chain is not None:
            chain = parent_chain
        elif parent_entity_type is not None and parent_name is not None:
            chain = [(parent_entity_type, parent_name)]
        else:
            raise TypeError("Must provide parent_chain or (parent_entity_type, parent_name)")

        include_predicates: List[str] = []
        exclude_predicates: List[str] = []
        exclude_has_match_all = False

        applicable_rules: List[str] = []
        for parsed_rule in self._parsed_rules_for_entity(child_entity_type):
            rule = str(parsed_rule["raw_rule"])
            is_include = parsed_rule["op"] == "!"
            pattern = str(parsed_rule["pattern"])
            match = _match_entity_pattern(child_entity_type, pattern)
            extracted = _extract_ancestor_child_patterns_from_match(child_entity_type, match) if match else None
            if not extracted or len(extracted[0]) != len(chain) or not _chain_matches(extracted[0], chain):
                continue

            ancestor_chain, child_identifier_pattern = extracted
            predicate = _identifier_pattern_to_tm1_filter(
                identifier_pattern=child_identifier_pattern,
                name_property=name_property,
            )
            if not predicate:
                continue

            if is_include:
                include_predicates.append(predicate)
            else:
                exclude_predicates.append(predicate)
                if _is_match_all_identifier(child_identifier_pattern):
                    exclude_has_match_all = True
            if rule not in applicable_rules:
                applicable_rules.append(rule)

        inherited_patterns, inherited_rules = self._force_include_patterns_for_target(
            target_entity_type=child_entity_type,
            ancestor_chain=chain,
        )
        for identifier_pattern in inherited_patterns:
            predicate = _identifier_pattern_to_tm1_filter(
                identifier_pattern=identifier_pattern,
                name_property=name_property,
            )
            if predicate and predicate not in include_predicates:
                include_predicates.append(predicate)
        for inherited_rule in inherited_rules:
            if inherited_rule not in applicable_rules:
                applicable_rules.append(inherited_rule)

        return _compose_tm1_filter_result(
            include_predicates=include_predicates,
            exclude_predicates=exclude_predicates,
            exclude_has_match_all=exclude_has_match_all,
            applicable_rules=applicable_rules,
        )

    def to_tm1_hierarchy_name_filter(self, dimension_name: str, name_property: str = "Name") -> Tm1FilterResult:
        """Build TM1 OData $filter for hierarchies under a specific dimension."""
        return self.to_tm1_child_name_filter(
            parent_chain=[(EntityType.DIMENSION, dimension_name)],
            child_entity_type=EntityType.HIERARCHY,
            name_property=name_property,
        )

    def to_tm1_element_name_filter(
        self, dimension_name: str, hierarchy_name: str, name_property: str = "Name"
    ) -> Tm1FilterResult:
        """Build TM1 OData $filter for elements under a specific dimension/hierarchy."""
        return self.to_tm1_child_name_filter(
            parent_chain=[
                (EntityType.DIMENSION, dimension_name),
                (EntityType.HIERARCHY, hierarchy_name),
            ],
            child_entity_type=EntityType.ELEMENT,
            name_property=name_property,
        )

    def to_tm1_subset_name_filter(
        self, dimension_name: str, hierarchy_name: str, name_property: str = "Name"
    ) -> Tm1FilterResult:
        """Build TM1 OData $filter for subsets under a specific dimension/hierarchy."""
        return self.to_tm1_child_name_filter(
            parent_chain=[
                (EntityType.DIMENSION, dimension_name),
                (EntityType.HIERARCHY, hierarchy_name),
            ],
            child_entity_type=EntityType.SUBSET,
            name_property=name_property,
        )

    def to_tm1_edge_name_filter(
        self, dimension_name: str, hierarchy_name: str
    ) -> Tm1FilterResult:
        """Build TM1 OData $filter for edges under a specific dimension/hierarchy.

        Edge is represented as {parentName}/{componentName}. Preferred filter format:
        Edges('parentNamePattern'/'componentPattern'), e.g.
        Edges('Total*'/'*') or Edges('*'/'Leaf*').
        """
        chain = [
            (EntityType.DIMENSION, dimension_name),
            (EntityType.HIERARCHY, hierarchy_name),
        ]
        include_predicates: List[str] = []
        exclude_predicates: List[str] = []
        exclude_has_match_all = False

        applicable_rules: List[str] = []
        for parsed_rule in self._parsed_rules_for_entity(EntityType.EDGE):
            rule = str(parsed_rule["raw_rule"])
            is_include = parsed_rule["op"] == "!"
            pattern = str(parsed_rule["pattern"])
            match = _match_entity_pattern(EntityType.EDGE, pattern)
            extracted = _extract_ancestor_child_patterns_from_match(EntityType.EDGE, match) if match else None
            if not extracted or len(extracted[0]) != len(chain) or not _chain_matches(extracted[0], chain):
                continue

            ancestor_chain, edge_pattern = extracted
            parts = edge_pattern.split("/", 1)
            parent_pattern = parts[0] if len(parts) >= 1 else "*"
            component_pattern = parts[1] if len(parts) >= 2 else "*"

            parent_pred = _identifier_pattern_to_tm1_filter(parent_pattern, "ParentName")
            component_pred = _identifier_pattern_to_tm1_filter(component_pattern, "ComponentName")
            if not parent_pred or not component_pred:
                continue

            predicate = f"({parent_pred}) and ({component_pred})"

            if is_include:
                include_predicates.append(predicate)
            else:
                exclude_predicates.append(predicate)
                if _is_match_all_identifier(edge_pattern):
                    exclude_has_match_all = True
            if rule not in applicable_rules:
                applicable_rules.append(rule)

        return _compose_tm1_filter_result(
            include_predicates=include_predicates,
            exclude_predicates=exclude_predicates,
            exclude_has_match_all=exclude_has_match_all,
            applicable_rules=applicable_rules,
        )

    @staticmethod
    def _has_supported_wildcard(identifier_pattern: str) -> bool:
        if "*" not in identifier_pattern:
            return True
        # Only startswith or endswith wildcard forms are supported.
        return identifier_pattern.startswith("*") or identifier_pattern.endswith("*")

    @staticmethod
    def _validate_url_rule_pattern(normalized_rule: str) -> bool:
        """
        Validate URL-style rule patterns.
        For object identifiers inside single quotes (e.g. Dimensions('x*')),
        wildcard '*' can only be at start or end, not in the middle.
        Edge identifiers use parent/component format; each part is validated separately.
        """
        if not normalized_rule:
            return True
        pattern = normalized_rule[1:].lstrip("/") if normalized_rule[0] == "!" else normalized_rule
        for identifier in re.findall(r"'([^']*)'", pattern):
            if "/" in identifier:
                for part in identifier.split("/"):
                    if not FilterRules._has_supported_wildcard(part):
                        return False
            elif not FilterRules._has_supported_wildcard(identifier):
                return False
        return True


def _top_level_prefix(path_or_pattern: str) -> str:
    """Extract top-level prefix for grouping: dimensions, cubes, processes, or ''."""
    lower = (path_or_pattern or "").lower()
    if lower.startswith("dimensions"):
        return "dimensions"
    if lower.startswith("cubes"):
        return "cubes"
    if lower.startswith("processes"):
        return "processes"
    if lower.startswith("chores"):
        return "chores"
    return ""


DEFAULT_TM1_TECHNICAL_OBJECTS_AND_LEAVES = [
    "Cubes('}*')",
    "Dimensions('}*')",
    "Processes('}*')",
    "Dimensions('*')/Hierarchies('Leaves')",
]

logger = logging.getLogger(__name__)


def _normalize_match_text(text: str) -> str:
    return (text or "").replace("\\", "/").lstrip("/").lower()


def _normalize_selector_text(text: str) -> str:
    return (text or "").replace("\\", "/").lstrip("/")


def _resolve_entity_type(entity: str | EntityType) -> Optional[EntityType]:
    if isinstance(entity, EntityType):
        return entity
    try:
        return EntityType((entity or "").lower())
    except ValueError:
        return None


def _split_rule_prefix(rule: str) -> tuple[bool, str]:
    is_include = rule.startswith("!")
    pattern = rule[1:] if is_include else rule
    return is_include, pattern


def _match_entity_pattern(entity_type: EntityType, pattern: str) -> Optional[re.Match[str]]:
    return re.fullmatch(_ENTITY_RULE_PATTERNS[entity_type], pattern, flags=re.IGNORECASE)


def _rule_matches_any_entity_pattern(pattern: str) -> bool:
    """Return True if the pattern matches at least one entity rule regex."""
    for entity_type in _ENTITY_RULE_PATTERNS:
        if _match_entity_pattern(entity_type, pattern) is not None:
            return True
    return False


def _extract_ancestor_child_patterns_from_match(
    entity_type: EntityType,
    match: re.Match[str],
) -> Optional[tuple[list[tuple[EntityType, str]], str]]:
    """Extract ancestor chain and child pattern from regex match.

    Returns (ancestor_chain, child_pattern) where ancestor_chain is
    [(EntityType, pattern), ...]. Returns None for unsupported entity types.
    """
    groups = match.groups()
    if entity_type == EntityType.HIERARCHY and len(groups) >= 2:
        return [(EntityType.DIMENSION, groups[0])], groups[1]
    if entity_type == EntityType.TASK and len(groups) >= 2:
        return [(EntityType.CHORE, groups[0])], groups[1]
    if entity_type == EntityType.VIEW and len(groups) >= 2:
        return [(EntityType.CUBE, groups[0])], groups[1]
    if entity_type == EntityType.ELEMENT and len(groups) >= 3:
        return [(EntityType.DIMENSION, groups[0]), (EntityType.HIERARCHY, groups[1])], groups[2]
    if entity_type == EntityType.SUBSET and len(groups) >= 3:
        return [(EntityType.DIMENSION, groups[0]), (EntityType.HIERARCHY, groups[1])], groups[2]
    if entity_type == EntityType.EDGE and len(groups) >= 7:
        # Groups: dim, hier, new_parent, new_component, old_parent, old_component, single.
        if groups[2] is not None and groups[3] is not None:
            edge_pattern = f"{groups[2]}/{groups[3]}"  # Edges('parent'/'component')
        elif groups[6] is not None:
            edge_pattern = groups[6]  # Edges('*')
        else:
            edge_pattern = f"{groups[4]}/{groups[5]}"  # Edges('parent/component')
        return [(EntityType.DIMENSION, groups[0]), (EntityType.HIERARCHY, groups[1])], edge_pattern
    return None


def _parse_selector_pattern(pattern: str) -> Optional[dict[str, Any]]:
    base_pattern, has_area_suffix, area_suffix = pattern.partition("|")
    area_pattern = normalize_for_path(area_suffix) if has_area_suffix else None
    for entity_type in _ENTITY_RULE_PATTERNS:
        match = _match_entity_pattern(entity_type, base_pattern)
        if not match:
            continue

        groups = match.groups()
        if entity_type in {EntityType.DIMENSION, EntityType.CUBE, EntityType.PROCESS, EntityType.CHORE}:
            return {
                "pattern": pattern,
                "entity_type": entity_type,
                "ancestor_chain": [],
                "identifier_pattern": groups[0],
                "area_pattern": area_pattern,
            }

        if entity_type == EntityType.RULE and len(groups) >= 2:
            return {
                "pattern": pattern,
                "entity_type": entity_type,
                "ancestor_chain": [(EntityType.CUBE, groups[0])],
                "identifier_pattern": groups[1],
                "area_pattern": area_pattern,
            }

        extracted = _extract_ancestor_child_patterns_from_match(entity_type, match)
        if extracted:
            ancestor_chain, identifier_pattern = extracted
            return {
                "pattern": pattern,
                "entity_type": entity_type,
                "ancestor_chain": ancestor_chain,
                "identifier_pattern": identifier_pattern,
                "area_pattern": area_pattern,
            }
    return None


def _parse_object_selector(object_url: str) -> Optional[dict[str, Any]]:
    parsed = _parse_selector_pattern(_normalize_selector_text(object_url))
    if not parsed:
        return None
    return {
        "entity_type": parsed["entity_type"],
        "ancestor_chain": parsed["ancestor_chain"],
        "identifier": parsed["identifier_pattern"],
        "area": parsed["area_pattern"],
    }


def _selector_rule_specificity(rule: dict[str, Any]) -> tuple[int, int, int, int]:
    pattern = rule.get("pattern", "")
    return (
        pattern.count("|"),
        pattern.count("/"),
        len(pattern),
        -pattern.count("*"),
    )


def _selector_rule_matches_context(rule: dict[str, Any], context: dict[str, Any]) -> bool:
    if rule["entity_type"] != context["entity_type"]:
        return False
    if len(rule["ancestor_chain"]) != len(context["ancestor_chain"]):
        return False
    if not _chain_matches(rule["ancestor_chain"], context["ancestor_chain"]):
        return False
    if not _identifier_pattern_matches(context["identifier"], rule["identifier_pattern"]):
        return False

    rule_area = rule.get("area_pattern")
    context_area = context.get("area")
    if rule_area is not None:
        if context_area is None:
            return False
        if not _identifier_pattern_matches(context_area, rule_area):
            return False

    return True


def should_exclude_path(object_uri: str, filter_rules: List[str]) -> bool:
    """Return True when the effective winning rule excludes the provided object URL."""
    rules = FilterRules(filter_rules)
    return rules.should_exclude(object_uri)


def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")


def _identifier_pattern_to_tm1_filter(identifier_pattern: str, name_property: str = "Name") -> Optional[str]:
    if identifier_pattern is None:
        return None

    escaped = _escape_odata_string(identifier_pattern)
    if escaped == "*":
        return f"startswith({name_property}, '')"
    if escaped.startswith("*") and len(escaped) > 1:
        return f"endswith({name_property}, '{escaped[1:]}')"
    if escaped.endswith("*") and len(escaped) > 1:
        return f"startswith({name_property}, '{escaped[:-1]}')"
    return f"{name_property} eq '{escaped}'"


def _identifier_pattern_matches(value: str, identifier_pattern: str) -> bool:
    return fnmatch.fnmatchcase(_normalize_match_text(value), _normalize_match_text(identifier_pattern))


def _join_or_predicates(predicates: List[str]) -> Optional[str]:
    if not predicates:
        return None
    if len(predicates) == 1:
        return predicates[0]
    return " or ".join(f"({predicate})" for predicate in predicates)


def _join_and_predicates(predicates: List[str]) -> Optional[str]:
    if not predicates:
        return None
    if len(predicates) == 1:
        return predicates[0]
    return " and ".join(f"({predicate})" for predicate in predicates)


def _compose_tm1_filter_expression(
    *,
    include_predicates: List[str],
    exclude_predicates: List[str],
) -> Optional[str]:
    include_clause = _join_or_predicates(include_predicates)
    exclude_terms = [f"not ({predicate})" for predicate in exclude_predicates]
    exclude_clause = _join_and_predicates(exclude_terms)

    if include_clause and exclude_clause:
        return f"({exclude_clause}) or ({include_clause})"
    if exclude_clause:
        return exclude_clause
    if include_clause:
        return include_clause
    return None


def _compose_tm1_filter_result(
    *,
    include_predicates: List[str],
    exclude_predicates: List[str],
    exclude_has_match_all: bool = False,
    applicable_rules: Optional[List[str]] = None,
) -> Tm1FilterResult:
    """Build Tm1FilterResult. skip_all=True when exclude-only and excludes match everything."""
    filter_expr = _compose_tm1_filter_expression(
        include_predicates=include_predicates,
        exclude_predicates=exclude_predicates,
    )
    skip_all = bool(
        not include_predicates
        and exclude_predicates
        and exclude_has_match_all
    )
    return Tm1FilterResult(
        filter_expr=filter_expr,
        skip_all=skip_all,
        applicable_rules=list(applicable_rules or []),
    )


def _perform_dependency_check(model: Model):
    kept_dim_names = {d.name for d in model.dimensions}
    model.cubes = [c for c in model.cubes if {d.name for d in c.dimensions}.issubset(kept_dim_names)]

    kept_process_names = {p.name for p in model.processes}
    model.chores = [ch for ch in model.chores if all(t.process_name in kept_process_names for t in ch.tasks)]


def normalize_for_path(text: str) -> str:
    chars_to_remove = "[]'\""
    for char in chars_to_remove:
        text = text.replace(char, '')

    text = text.replace(',', '_').replace(':', '_').replace(' ', '')

    return text.lower()


def import_filter(path: str) -> List[str]:
    rules_path = path
    filter_rules: List[str] = []
    with open(rules_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            # Lines starting with "#" are comments in filter files.
            if not stripped or stripped.startswith('#'):
                continue
            filter_rules.append(stripped)
    logger.info("Loaded %d filter rule(s) from '%s'", len(filter_rules), path)
    return filter_rules


def _expand_removed_paths(paths_to_remove: set[str], all_paths: List[str]) -> set[str]:
    def _is_descendant_path(parent: str, candidate: str) -> bool:
        if not parent or not candidate or candidate == parent:
            return False
        return (
            candidate.startswith(parent + "|") or
            candidate.startswith(parent + "/") or
            candidate.startswith(parent + ".")
        )

    expanded_paths_to_remove = set(paths_to_remove)
    for path_to_remove in paths_to_remove:
        for path in all_paths:
            if _is_descendant_path(path_to_remove, path):
                expanded_paths_to_remove.add(path)
    return expanded_paths_to_remove


def filter(model: Model, filter_rules: List[str]) -> Model:
    if not filter_rules:
        logger.debug("No filter rules provided, returning original model")
        return model

    logger.info(
        "Applying model filter rules (rules=%d dimensions=%d cubes=%d processes=%d chores=%d)",
        len(filter_rules),
        len(model.dimensions),
        len(model.cubes),
        len(model.processes),
        len(model.chores),
    )
    rules = FilterRules(filter_rules)
    removed_parent_paths: set[str] = set()

    def _is_descendant_of_removed(path: str) -> bool:
        for parent in removed_parent_paths:
            if path.startswith(parent + "|") or path.startswith(parent + "/") or path.startswith(parent + "."):
                return True
        return False

    def _should_remove(path: Optional[str]) -> bool:
        if not path:
            return False
        if _is_descendant_of_removed(path):
            return True
        if rules.should_exclude(path):
            removed_parent_paths.add(path)
            return True
        return False

    def _filter_collection_streaming(
        collection: Any,
        item_url_fn: Callable[[Any], Optional[str]],
    ) -> Any:
        # Explicitly stream disk-backed collections through their underlying JSONL payloads.
        if isinstance(collection, StoreBackedSequence):
            # Materialize first: replace_with_payloads clears the underlying store.
            kept_payloads: list[dict] = []
            for payload in collection.iter_payloads():
                item = collection.item_from_payload(payload)
                if _should_remove(item_url_fn(item)):
                    continue
                kept_payloads.append(payload)

            collection.replace_with_payloads(kept_payloads)
            return collection

        return [item for item in collection if not _should_remove(item_url_fn(item))]

    final_dims = []
    for dim in model.dimensions:
        dim_url = dim.uri()
        if _should_remove(dim_url):
            continue

        kept_hierarchies = []
        for hierarchy in dim.hierarchies:
            hierarchy_url = hierarchy.uri(dim.name)
            if _should_remove(hierarchy_url):
                continue

            hierarchy.subsets = _filter_collection_streaming(
                hierarchy.subsets,
                lambda subset: subset.uri(dim.name, hierarchy.name),
            )
            hierarchy.elements = _filter_collection_streaming(
                hierarchy.elements,
                lambda element: element.uri(dim.name, hierarchy.name),
            )
            hierarchy.edges = _filter_collection_streaming(
                hierarchy.edges,
                lambda edge: edge.uri(dim.name, hierarchy.name),
            )
            kept_hierarchies.append(hierarchy)

        dim.hierarchies = kept_hierarchies
        final_dims.append(dim)
    final_procs = [proc for proc in model.processes if not _should_remove(proc.uri())]

    final_cubes = []
    for cube in model.cubes:
        cube_url = cube.uri()
        if not _should_remove(cube_url):

            kept_rules = []
            for rule in cube.rules:
                rule_url = f"{rule.uri(cube.name)}|{normalize_for_path(rule.area)}"
                if not _should_remove(rule_url):
                    kept_rules.append(rule)
            cube.rules = kept_rules

            cube.views = [
                view for view in cube.views
                if not _should_remove(view.uri(cube.name))
            ]

            final_cubes.append(cube)

    final_chores = []
    for chore in model.chores:
        chore_url = chore.uri()
        if not _should_remove(chore_url):
            final_chores.append(chore)

    filtered_model = Model(
        cubes=final_cubes,
        dimensions=final_dims,
        processes=final_procs,
        chores=final_chores
    )

    # _perform_dependency_check(filtered_model)
    logger.info(
        "Filter finished (removed_paths=%d dimensions=%d cubes=%d processes=%d chores=%d)",
        len(removed_parent_paths),
        len(filtered_model.dimensions),
        len(filtered_model.cubes),
        len(filtered_model.processes),
        len(filtered_model.chores),
    )

    return filtered_model


def _normalize_filter_path(path: str) -> str:
    path = (path or "").strip().lstrip("/")
    if not path:
        return ""
    if "|[" in path:
        cube_part, area_part = path.rsplit("|", 1)
        return f"{normalize_reference_path(cube_part)}|{normalize_for_path(area_part)}"
    return normalize_reference_path(path)


def filter_changeset(
        changeset: Changeset,
        filter_rules: Mapping[str, List[str]],
        *,
        filter_children: Optional[bool] = False
) -> Changeset:
    if not filter_rules:
        logger.debug("No changeset filter rules provided, returning original changeset")
        return changeset

    def _change_path(change: Change) -> str:
        return _normalize_filter_path(getattr(change, "uri", "") or "")

    path_entries: list[tuple[str, str, Change]] = []
    for change in changeset.changes:
        section = ChangeType.from_raw(change.change_type).value
        path_entries.append((section, _change_path(change), change))

    paths_by_section: dict[str, list[str]] = {"add": [], "remove": [], "modify": []}
    for section, path, _change in path_entries:
        if path:
            paths_by_section[section].append(path)

    paths_to_remove_by_section: dict[str, set[str]] = {"add": set(), "remove": set(), "modify": set()}

    rules_by_section = {
        "add": {_normalize_filter_path(path) for path in (filter_rules.get("add", []) or []) if path},
        "remove": {_normalize_filter_path(path) for path in (filter_rules.get("remove", []) or []) if path},
        "modify": {_normalize_filter_path(path) for path in (filter_rules.get("modify", []) or []) if path},
    }

    for section, path, _change in path_entries:
        if not path:
            continue
        section_paths = rules_by_section.get(section, set())
        if path in section_paths:
            paths_to_remove_by_section[section].add(path)

    expanded_paths_to_remove_by_section: dict[str, set[str]] = {"add": set(), "remove": set(), "modify": set()}
    for section in ("add", "remove", "modify"):
        if filter_children:
            expanded_paths_to_remove_by_section[section] = _expand_removed_paths(
                paths_to_remove_by_section[section],
                paths_by_section[section]
            )
        else:
            expanded_paths_to_remove_by_section[section] = set(paths_to_remove_by_section[section])

    filtered_changeset = Changeset()
    filtered_changeset.changes = [
        change for section, path, change in path_entries
        if path not in expanded_paths_to_remove_by_section[section]
    ]
    filtered_changeset.errors = dict(changeset.errors)
    filtered_changeset.last_execution_id = changeset.last_execution_id
    logger.info(
        "Filtered changeset from %d to %d change(s) (filter_children=%s)",
        len(changeset.changes),
        len(filtered_changeset.changes),
        filter_children,
    )

    return filtered_changeset

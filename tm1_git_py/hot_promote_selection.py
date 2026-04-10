import re

from tm1_git_py.changeset import Changeset, ObjectType

SUPPORTED_SELECTION_CATEGORIES = {
    "dimensions": ObjectType.DIMENSION,
    "hierarchies": ObjectType.HIERARCHY,
    "subsets": ObjectType.SUBSET,
    "elements": ObjectType.ELEMENT,
    "edges": ObjectType.EDGE,
    "cubes": ObjectType.CUBE,
    "mdxviews": ObjectType.MDX_VIEW,
    "nativeviews": ObjectType.NATIVE_VIEW,
    "processes": ObjectType.PROCESS,
    "chores": ObjectType.CHORE,
}

_SELECTION_PATTERNS = {
    "dimensions": re.compile(r"^Dimensions\('([^']+|\*)'\)$"),
    "hierarchies": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)$"),
    "subsets": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Subsets\('([^']+|\*)'\)$"),
    "elements": re.compile(r"^Dimensions\('([^']+|\*)'\)/Hierarchies\('([^']+|\*)'\)/Elements\('([^']+|\*)'\)$"),
    "edges": (
        r"^Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)/Edges\("
        r"(?:'([^']*)'/'([^']*)'|'([^'/]*)/([^']*)'|'([^']*)')"
        r"\)$"
    ),
    "cubes": re.compile(r"^Cubes\('([^']+|\*)'\)$"),
    "mdxviews": re.compile(r"^Cubes\('([^']+|\*)'\)/Views\('([^']+|\*)'\)$"),
    "nativeviews": re.compile(r"^Cubes\('([^']+|\*)'\)/Views\('([^']+|\*)'\)$"),
    "processes": re.compile(r"^Processes\('([^']+|\*)'\)$"),
    "chores": re.compile(r"^Chores\('([^']+|\*)'\)$"),
}


def validate_selection_category(category: str) -> str:
    normalized = (category or "").strip().lower()
    if normalized not in SUPPORTED_SELECTION_CATEGORIES:
        raise ValueError(f"Unsupported selection category '{category}'.")
    return normalized


def _match_uri_parts(category: str, uri: str) -> tuple[str, ...]:
    pattern = _SELECTION_PATTERNS[category]
    match = pattern.fullmatch(uri or "")
    if not match:
        raise ValueError(f"Invalid {category} selection uri: '{uri}'")
    return tuple(str(group) for group in match.groups())


def matches_selection(category: str, selection_uri: str, change_uri: str) -> bool:
    normalized_category = validate_selection_category(category)
    selection_parts = _match_uri_parts(normalized_category, selection_uri)
    change_parts = _match_uri_parts(normalized_category, change_uri)
    return all(expected == "*" or expected == actual for expected, actual in zip(selection_parts, change_parts))


def update_changeset_apply(changeset: Changeset, category: str, uri: str, apply: bool) -> int:
    normalized_category = validate_selection_category(category)
    if not isinstance(apply, bool):
        raise ValueError("apply must be a boolean")

    _match_uri_parts(normalized_category, uri)
    target_object_type = SUPPORTED_SELECTION_CATEGORIES[normalized_category]
    updated = 0
    for change in changeset.changes:
        if change.object_type != target_object_type:
            continue
        if matches_selection(normalized_category, uri, change.uri):
            change.apply = apply
            updated += 1
    return updated

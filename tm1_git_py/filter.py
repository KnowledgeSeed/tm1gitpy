import fnmatch
from typing import Any, List, Mapping, Optional

from tm1_git_py.changeset import Change, Changeset, ChangeType, normalize_source_path
from tm1_git_py.model import Model


DEFAULT_TM1_TECHNICAL_OBJECTS = ["-/cubes/}*", "-/dimensions/}*", "-/processes/}*"]


def _normalize_match_text(text: str) -> str:
    return (text or "").replace("\\", "/").lstrip("/").lower()


def _normalize_rule_for_matching(rule: str) -> str:
    if not rule:
        return rule
    op = rule[0]
    if op not in {"+", "-"}:
        return _normalize_match_text(rule)
    pattern = rule[1:].lstrip("/")
    return f"{op}{pattern.lower()}"


def should_exclude_path(path: str, filter_rules: List[str]) -> bool:
    """Return True when the effective winning rule excludes the provided object path."""
    if not filter_rules:
        return False

    normalized_path = _normalize_match_text(path)
    normalized_rules = [_normalize_rule_for_matching(rule) for rule in filter_rules]
    winning_rule = _get_winning_rule(normalized_path, normalized_rules)
    return bool(winning_rule and winning_rule["op"] == "-")

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
    filter_rules = []
    with open(rules_path, 'r', encoding='utf-8') as f:
        filter_rules = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    return filter_rules


def _get_winning_rule(path: str, filter_rules: List[str]) -> dict[str, str] | None:
    matching_rules = []
    for rule in filter_rules:
        if len(rule) < 2 or rule[0] not in ['+', '-']:
            continue
        op, pattern = rule[0], rule[1:].lstrip('/')

        is_match = False

        if '|[' in pattern:
            cube_part, area_part = pattern.rsplit('|', 1)
            normalized_pattern = f"{cube_part}|{normalize_for_path(area_part)}"
            if path == normalized_pattern:
                is_match = True
        else:
            last_part = pattern.split('|')[-1] if '|' in pattern else ""
            is_indexless_task = '|' in pattern and not last_part.isdigit()

            if is_indexless_task:
                if path.startswith(pattern + '|'):
                    is_match = True
            else:
                if fnmatch.fnmatch(path, pattern):
                    is_match = True

        if is_match:
            matching_rules.append({'op': op, 'pattern': pattern})

    if not matching_rules:
        return None

    return max(
        matching_rules,
        key=lambda r: (r['pattern'].count('|'), r['pattern'].count('/'), len(r['pattern']),
                       -r['pattern'].count('*'))
    )


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
        return model

    all_objects = model.get_all_objects_with_paths()
    paths_to_remove = set()

    for path in all_objects.keys():
        winning_rule = _get_winning_rule(path, filter_rules)
        if not winning_rule:
            continue

        if winning_rule['op'] == '-':
            paths_to_remove.add(path)

    expanded_paths_to_remove = _expand_removed_paths(paths_to_remove, list(all_objects.keys()))

    final_dims = [dim for dim in model.dimensions if dim.source_path.replace('\\', '/') not in expanded_paths_to_remove]
    final_procs = [proc for proc in model.processes if
                   proc.source_path.replace('\\', '/') not in expanded_paths_to_remove]

    final_cubes = []
    for cube in model.cubes:
        cube_path = cube.source_path.replace('\\', '/')
        if cube_path not in expanded_paths_to_remove:

            kept_rules = []
            for rule in cube.rules:
                normalized_area = normalize_for_path(rule.area)
                rule_path = f"{cube_path}|{normalized_area}"
                if rule_path not in expanded_paths_to_remove:
                    kept_rules.append(rule)
            cube.rules = kept_rules

            final_cubes.append(cube)

    final_chores = []
    for chore in model.chores:
        chore_path = chore.source_path.replace('\\', '/')
        if chore_path not in expanded_paths_to_remove:
            kept_tasks = []
            for i, task in enumerate(chore.tasks):
                task_path = f"{chore_path}|{task.process_name}|{i}"
                if task_path not in expanded_paths_to_remove:
                    kept_tasks.append(task)
            chore.tasks = kept_tasks
            final_chores.append(chore)

    filtered_model = Model(
        cubes=final_cubes,
        dimensions=final_dims,
        processes=final_procs,
        chores=final_chores
    )

    # _perform_dependency_check(filtered_model)

    return filtered_model


def _normalize_path(obj: Any) -> str:
    return normalize_source_path(getattr(obj, "source_path", ""))


def _normalize_filter_path(path: str) -> str:
    path = (path or "").strip().lstrip("/")
    if not path:
        return ""
    if "|[" in path:
        cube_part, area_part = path.rsplit("|", 1)
        return f"{normalize_source_path(cube_part)}|{normalize_for_path(area_part)}"
    return normalize_source_path(path)


def filter_changeset(
        changeset: Changeset,
        filter_rules: Mapping[str, List[str]],
        *,
        filter_children: Optional[bool] = False
) -> Changeset:
    if not filter_rules:
        return changeset

    def _change_path(change: Change) -> str:
        body_path = _normalize_path(change.body)
        if body_path:
            return body_path
        return normalize_source_path(change.source_path)

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
    filtered_changeset.sort()

    return filtered_changeset

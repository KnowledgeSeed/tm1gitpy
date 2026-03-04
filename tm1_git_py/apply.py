import importlib
import json
import logging
import re
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Optional, Union, TypeVar

from TM1py import TM1Service
from requests import Response

from tm1_git_py import Changeset
from tm1_git_py.changeset import ChangeType, Change, ObjectType
from tm1_git_py.changeset_status import ChangeSetStatusStore
from tm1_git_py.model import Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule
from tm1_git_py.validation import validate_changeset

logger = logging.getLogger(__name__)

T = TypeVar('T', Cube, MDXView, Dimension, Hierarchy, Subset, Process, Chore, Element, Edge, Rule)


def apply(
        changeset: Changeset,
        tm1_service: TM1Service,
        *,
        status_dir: Optional[Union[str, Path]] = None,
        execution_id: Optional[str] = None,
        changeset_name: Optional[str] = None,
        fail_fast: bool = True
) -> tuple[bool, Union[list, None]]:

    changes = []
    if not changeset.has_changes():
        logger.info("No changes to apply.")
        return True, None

    execution_changes = _prepare_execution_changes(changeset.changes, tm1_service)
    """    
    validate_errors = validate_changeset(
        tm1_service=tm1_service,
        changeset_object=changeset,
        fail_fast=fail_fast
    )
    if validate_errors:
        logger.warning("Changeset validation reported %d error(s).", len(validate_errors))
    """

    store: Optional[ChangeSetStatusStore] = None
    if status_dir is not None:
        store = ChangeSetStatusStore(status_dir=status_dir, execution_id=execution_id,
                                     changeset_name=changeset_name)
        store.start(total_operations=len(execution_changes))
        changeset.last_execution_id = store.execution_id
        logger.info("changeset execution_id=%s status_file=%s", store.execution_id, store.path)

    ok_all = True

    for i, change in enumerate(execution_changes, start=1):
        obj = change.body
        action = ChangeType.from_raw(change.change_type)
        action_name = action.value
        obj_type = change.object_type.value
        obj_path = change.source_path
        obj_name = obj.name

        if store is not None:
            store.begin_operation(i, action_name, obj_type, obj_name, obj_path)

        try:
            if action == ChangeType.ADD:
                resp = create_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            elif action == ChangeType.MODIFY:
                resp = update_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            elif action == ChangeType.REMOVE:
                resp = delete_object(tm1_service=tm1_service, object_instance=obj, object_type=obj_type)
            else:
                raise ValueError(f"Unknown action: {action_name}")

            changes.append(resp.url)

            logger.info("%s %s%s -> %s %s",
                        action_name,
                        f"{obj_type}:" if obj_name else obj_type,
                        obj_name or "",
                        resp.status_code,
                        getattr(resp, "url", ""))

            if store is not None:
                store.end_operation_with_response(resp)

            if not resp.ok:
                ok_all = False
                if fail_fast:
                    if store is not None:
                        store.fail()
                    return False, changes

        except Exception as exc:
            logger.exception("Exception during %s %s%s: %s",
                             action_name,
                             f"{obj_type}:" if obj_name else obj_type,
                             obj_name or "",
                             exc)
            if store is not None:
                store.end_operation_with_exception(exc)
                store.fail()
            return False, changes

    if store is not None:
        store.succeed() if ok_all else store.fail()

    return ok_all, changes


# --------------------------------------------------------------------------------
# CRUD operations for apply changeset function
# --------------------------------------------------------------------------------

def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _resolve_handler(module, action: str, object_type: str):
    candidates = [
        f"{action}_{object_type.lower()}",
        f"{action}_{_camel_to_snake(object_type)}",
    ]
    for candidate in candidates:
        fn = getattr(module, candidate, None)
        if fn is not None:
            return fn
    raise AttributeError(
        f"No handler found for action='{action}', object_type='{object_type}' "
        f"in module '{module.__name__}'. Tried: {candidates}"
    )


def create_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    create = _resolve_handler(module, "create", object_type)
    return create(tm1_service, object_instance)


def delete_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    delete = _resolve_handler(module, "delete", object_type)
    return delete(tm1_service, object_instance)


def update_object(tm1_service: TM1Service, object_instance: T, object_type) -> Response:
    module = importlib.import_module(object_instance.__class__.__module__)
    update = _resolve_handler(module, "update", object_type)
    return update(tm1_service, object_instance)


def _cube_name_from_rule_source_path(source_path: str) -> str:
    normalized = (source_path or "").replace("\\", "/").lstrip("/")
    match = re.match(r"cubes/(.+)\.rules$", normalized)
    if not match:
        raise ValueError(f"Invalid rule source_path: '{source_path}'")
    return match.group(1)


def _parse_rule_text(rule_text: str, cube_name: str) -> list[Rule]:
    if not rule_text:
        return []

    rules: list[Rule] = []
    seen_names: dict[str, int] = {}

    def _unique_name(area: str) -> str:
        base = Rule.name_from_area(area)
        seen_names[base] = seen_names.get(base, 0) + 1
        return base if seen_names[base] == 1 else f"{base}_{seen_names[base]}"

    pattern = re.compile(r"(?P<comment>(?:#.*(?:\r\n|\n|$)\s*)*)?(?P<statement>\[.*?\][^;]*;)", re.DOTALL)
    header_match = re.match(r"^(.*?)(?=\[|#|$)", rule_text, re.DOTALL)
    last_pos = 0

    if header_match:
        header_text = header_match.group(1).strip()
        if header_text:
            rules.append(
                Rule(
                    name=_unique_name("[HEADER]"),
                    area="[HEADER]",
                    full_statement=header_text,
                    comment="",
                    cube_name=cube_name,
                )
            )
        last_pos = header_match.end()

    for match in pattern.finditer(rule_text, last_pos):
        comment = (match.group("comment") or "").strip()
        statement_text = match.group("statement").strip()
        area_match = re.search(r"(\[.*?\])", statement_text)
        area = area_match.group(1) if area_match else "[UNKNOWN]"
        rules.append(
            Rule(
                name=_unique_name(area),
                area=area,
                full_statement=statement_text,
                comment=comment,
                cube_name=cube_name,
            )
        )
    return rules


def _get_live_cube_rules(tm1_service: TM1Service, cube_name: str) -> list[Rule]:
    cube = tm1_service.cubes.get(cube_name=cube_name)
    if not cube or not cube.rules:
        return []

    raw_body = cube.rules.body
    rule_text = ""
    try:
        parsed = json.loads(raw_body)
        if isinstance(parsed, dict):
            rule_text = parsed.get("Rules", "")
    except Exception:
        rule_text = raw_body if isinstance(raw_body, str) else ""
    return _parse_rule_text(rule_text, cube_name)


def _prepare_execution_changes(changes: list[Change], tm1_service: TM1Service) -> list[Change]:
    non_rule_changes: list[Change] = []
    rule_changes_by_cube: dict[str, list[Change]] = defaultdict(list)

    for change in changes:
        if change.object_type == ObjectType.RULE:
            cube_name = _cube_name_from_rule_source_path(change.source_path)
            rule_changes_by_cube[cube_name].append(change)
        else:
            non_rule_changes.append(change)

    synthesized_cube_changes: list[Change] = []
    for cube_name, cube_rule_changes in rule_changes_by_cube.items():
        # Use an ordered map so we can apply add/remove/modify by rule name while preserving order.
        live_rules = _get_live_cube_rules(tm1_service, cube_name)
        rules_by_name: OrderedDict[str, Rule] = OrderedDict((rule.name, rule) for rule in live_rules)

        for change in cube_rule_changes:
            action = ChangeType.from_raw(change.change_type)
            rule_obj = change.body
            if action == ChangeType.REMOVE:
                rules_by_name.pop(rule_obj.name, None)
            elif action in (ChangeType.ADD, ChangeType.MODIFY):
                rules_by_name[rule_obj.name] = rule_obj

        synthesized_cube_changes.append(
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.CUBE,
                source_path=f"cubes/{cube_name}.json",
                body=Cube(
                    name=cube_name,
                    dimensions=[],
                    rules=list(rules_by_name.values()),
                    views=[],
                    source_path=f"cubes/{cube_name}.json",
                ),
            )
        )

    execution_changes = non_rule_changes + synthesized_cube_changes
    temp = Changeset()
    temp.changes = execution_changes
    temp.sort()
    return temp.changes

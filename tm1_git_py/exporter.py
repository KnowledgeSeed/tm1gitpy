import json
import logging
import os
import re
from typing import Dict, List, Optional
from TM1py import TM1Service
import TM1py

from tm1_git_py import filter as filter_module
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.edge import Edge
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.model import Model
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI


logger = logging.getLogger(__name__)


def _compose_export_filter_rules(
    filter_rules: Optional[list[str]] = None,
) -> list[str]:
    effective_rules: list[str] = []
    if filter_rules:
        effective_rules.extend(filter_rules)
    return effective_rules


def _normalize_rule_for_winning_rule(rule: str) -> str:
    if not rule:
        return rule
    op = rule[0]
    if op not in {"+", "-"}:
        return rule.replace("\\", "/").lstrip("/").lower()
    pattern = rule[1:].lstrip("/").lower()
    return f"{op}{pattern}"


def _should_enable_skip_control_flags(filter_rules: list[str]) -> bool:
    if not filter_rules:
        return False

    normalized_rules = [_normalize_rule_for_winning_rule(rule) for rule in filter_rules]
    technical_patterns = {
        _normalize_rule_for_winning_rule(rule)[1:]
        for rule in filter_module.DEFAULT_TM1_TECHNICAL_OBJECTS
        if rule and rule[0] in {"+", "-"}
    }

    probe_paths = [
        "cubes/}technical_probe",
        "dimensions/}technical_probe",
        "processes/}technical_probe"
    ]
    for probe_path in probe_paths:
        winning_rule = filter_module._get_winning_rule(probe_path, normalized_rules)
        if winning_rule and winning_rule.get("op") == "-" and winning_rule.get("pattern") in technical_patterns:
            return True
    return False


def export(
    tm1_conn: TM1Service,
    filter_rules: Optional[list[str]] = None,
) -> tuple[Model, Dict[str, str]]:
    logger.info("TM1 export started")
    effective_rules = _compose_export_filter_rules(filter_rules=filter_rules)
    use_skip_control_flags = _should_enable_skip_control_flags(effective_rules)
    logger.info(
        "Export filters configured additional_rules=%d effective_rules=%d use_skip_control=%s",
        len(filter_rules or []),
        len(effective_rules),
        use_skip_control_flags,
    )

    _dimensions, _dim_errors = dimensions_to_model(
        tm1_conn,
        effective_rules=effective_rules,
        skip_control_dims=use_skip_control_flags,
    )

    _cubes, _cube_errors = cubes_to_model(
        tm1_conn,
        _dimensions,
        effective_rules=effective_rules,
        skip_control_cubes=use_skip_control_flags,
    )

    _processes, _process_errors = procs_to_model(
        tm1_conn,
        effective_rules=effective_rules,
        skip_control_processes=use_skip_control_flags,
    )

    _chores, _chore_errors = chores_to_model(tm1_conn, effective_rules=effective_rules)

    _model = Model(cubes=list(_cubes.values()),
                   dimensions=list(_dimensions.values()),
                   processes=list(_processes.values()),
                   chores=list(_chores.values()),
                   #server_configs=server_configs_to_model(tm1_conn)
                   )
    logger.info(
        "TM1 export model assembled dimensions=%d cubes=%d processes=%d chores=%d",
        len(_model.dimensions),
        len(_model.cubes),
        len(_model.processes),
        len(_model.chores),
    )
    _errors = {}
    _errors['dim'] = _dim_errors
    _errors['cube'] = _cube_errors
    _errors['process'] = _process_errors
    _errors['chore'] = _chore_errors

    total_errors = sum(len(category_errors) for category_errors in _errors.values())
    if total_errors:
        logger.warning(
            "TM1 export completed with errors: dimensions=%d cubes=%d processes=%d chores=%d",
            len(_dim_errors),
            len(_cube_errors),
            len(_process_errors),
            len(_chore_errors),
        )
    else:
        logger.info("TM1 export completed without errors")

    return _model, _errors


def chores_to_model(tm1_conn, effective_rules: Optional[list[str]] = None) -> tuple[Dict[str, Chore], Dict[str, str]]:
    all_chores = tm1_conn.chores.get_all_names()
    _chores: Dict[str, Chore] = {}
    _errors: Dict[str, str] = {}
    skipped_chores = 0
    skipped_tasks = 0
    logger.info("Exporting %d chores", len(all_chores))

    for chore_name in all_chores:
        chore_source_path = os.path.join('chores', f"{chore_name}.json").replace('\\', '/')
        if filter_module.should_exclude_path(chore_source_path, effective_rules or []):
            logger.debug("Skipping chore by filter: %s", chore_source_path)
            skipped_chores += 1
            continue

        chore = tm1_conn.chores.get(chore_name=chore_name)

        tasks_for_model = []
        for index, tm1py_task in enumerate(chore.tasks):
            task_dict = tm1py_task.body_as_dict
            process_name = ""
            process_bind = task_dict.get("Process@odata.bind", "")
            match = re.search(r"Processes\('([^']*)'\)", process_bind)
            if match:
                process_name = match.group(1)
            task_path = f"{chore_source_path}|{process_name}|{index}"
            if filter_module.should_exclude_path(task_path, effective_rules or []):
                logger.debug("Skipping chore task by filter: %s", task_path)
                skipped_tasks += 1
                continue

            task_obj = Task(
                process_name=process_name,
                parameters=task_dict.get('Parameters', [])
            )
            tasks_for_model.append(task_obj)

        _chore = Chore(
            name=chore.name,
            start_time=chore.start_time.start_time_string,
            dst_sensitive=chore.dst_sensitivity,
            active=chore.active,
            execution_mode=chore.execution_mode,
            frequency=chore.frequency.frequency_string,
            tasks=tasks_for_model,
            source_path=chore_source_path
        )
        _chores[chore.name] = _chore

    logger.info(
        "Chore export assembly finished total=%d kept=%d skipped_chores=%d skipped_tasks=%d",
        len(all_chores),
        len(_chores),
        skipped_chores,
        skipped_tasks,
    )
    return _chores, _errors


def procs_to_model(
    tm1_conn,
    effective_rules: Optional[list[str]] = None,
    skip_control_processes: bool = False,
) -> tuple[Dict[str, Process], Dict[str, str]]:
    all_procs = tm1_conn.processes.get_all_names(skip_control_processes=skip_control_processes)

    _processes: Dict[str, Process] = {}
    _errors: Dict[str, str] = {}
    skipped_processes = 0
    logger.info("Exporting %d processes", len(all_procs))

    for process_name in all_procs:
        process_source_path = os.path.join('processes', f"{process_name}.json").replace('\\', '/')
        if filter_module.should_exclude_path(process_source_path, effective_rules or []):
            logger.debug("Skipping process by filter: %s", process_source_path)
            skipped_processes += 1
            continue

        process = tm1_conn.processes.get(name_process=process_name)

        _ti = TI(prolog_procedure=process.prolog_procedure,
                 metadata_procedure=process.metadata_procedure,
                 data_procedure=process.data_procedure,
                 epilog_procedure=process.epilog_procedure)
        _process = Process(name=process.name, hasSecurityAccess=process.has_security_access,
                           code_link=process_name + '.ti',
                           datasource='',
                           parameters=process.parameters, variables=process.variables, ti=_ti,
                           source_path=process_source_path)
        _processes[process.name] = _process
    logger.info(
        "Process export assembly finished total=%d kept=%d skipped=%d",
        len(all_procs),
        len(_processes),
        skipped_processes,
    )
    return _processes, _errors


def cubes_to_model(
    tm1_conn: TM1Service,
    _dimensions: Dict[str, Dimension],
    effective_rules: Optional[list[str]] = None,
    skip_control_cubes: bool = False,
) -> tuple[Dict[str, Cube], Dict[str, str]]:
    all_cubes = tm1_conn.cubes.get_all_names(skip_control_cubes=skip_control_cubes)

    _cubes: Dict[str, Cube] = {}
    _errors: Dict[str, str] = {}
    skipped_cubes = 0
    skipped_rules = 0
    skipped_views = 0
    logger.info("Exporting %d cubes", len(all_cubes))

    for cube_name in all_cubes:
        cube_source_path = os.path.join('cubes', cube_name).replace('\\', '/')
        if filter_module.should_exclude_path(cube_source_path, effective_rules or []):
            logger.debug("Skipping cube by filter: %s", cube_source_path)
            skipped_cubes += 1
            continue

        try:
            cube = tm1_conn.cubes.get(cube_name=cube_name)

            #rule_source_object = cube.rules if cube.has_rules else None

            rule_text = ""
            if cube.has_rules:
                raw_body = cube.rules.body
                try:
                    rule_data = json.loads(raw_body)
                    rule_text = rule_data.get("Rules", "")
                except (json.JSONDecodeError, AttributeError):
                    rule_text = raw_body if isinstance(raw_body, str) else ""

            rules_list = _parse_rules(rule_text, cube_name=cube_name)
            filtered_rules_list = []
            for rule in rules_list:
                rule_path = f"{cube_source_path}|{filter_module.normalize_for_path(rule.area)}"
                if filter_module.should_exclude_path(rule_path, effective_rules or []):
                    logger.debug("Skipping rule by filter: %s", rule_path)
                    skipped_rules += 1
                    continue
                filtered_rules_list.append(rule)
            _cube = Cube(
                name=cube_name,
                dimensions=[],
                rules=filtered_rules_list,
                views=[],
                source_path=cube_source_path
            )

            skip_cube_due_to_filtered_dimension = False
            if cube.dimensions:
                for dimension in cube.dimensions:
                    _dimension = _dimensions.get(dimension)
                    if not _dimension:
                        dimension_source_path = os.path.join("dimensions", f"{dimension}.json").replace("\\", "/")
                        if filter_module.should_exclude_path(dimension_source_path, effective_rules or []):
                            logger.debug(
                                "Skipping cube '%s' because dependent dimension is filtered: %s",
                                cube_name,
                                dimension_source_path,
                            )
                            skip_cube_due_to_filtered_dimension = True
                            break
                        logger.warning(
                            "Cube '%s' references missing dimension '%s'",
                            cube_name,
                            dimension,
                        )
                        _errors[cube_name] = f"Dimension '{dimension}' not found"
                        skip_cube_due_to_filtered_dimension = True
                        break
                    else:
                        _cube.dimensions.append(_dimension)

            if skip_cube_due_to_filtered_dimension:
                skipped_cubes += 1
                continue

            _cubes[cube_name] = _cube

            views_tuple = tm1_conn.views.get_all(cube_name=cube_name)
            if views_tuple:
                private_views, public_views = views_tuple
                for view in private_views + public_views:
                    view_source_path = os.path.join('cubes', f"{cube_name}.views", f"{view.name}.json").replace('\\', '/')
                    if filter_module.should_exclude_path(view_source_path, effective_rules or []):
                        logger.debug("Skipping cube view by filter: %s", view_source_path)
                        skipped_views += 1
                        continue
                    if isinstance(view, TM1py.Objects.MDXView):
                        _view = MDXView(
                            name=view.name,
                            mdx=view.mdx,
                            source_path=view_source_path,
                        )
                    elif isinstance(view, TM1py.Objects.NativeView):
                        _view = NativeView(
                            name=view.name,
                            columns=view.columns,
                            rows=view.rows,
                            titles=view.titles,
                            suppress_empty_columns=view.suppress_empty_columns,
                            suppress_empty_rows=view.suppress_empty_rows,
                            format_string=view.format_string,
                            source_path=view_source_path,
                        )
                    else:
                        continue
                    _cube.views.append(_view)


        except Exception as e:
            logger.error("Failed to export cube '%s'", cube_name, exc_info=True)
            _errors[cube_name] = str(e)

    logger.info(
        "Cube export assembly finished total=%d kept=%d skipped_cubes=%d skipped_rules=%d skipped_views=%d",
        len(all_cubes),
        len(_cubes),
        skipped_cubes,
        skipped_rules,
        skipped_views,
    )
    return _cubes, _errors


def dimensions_to_model(
    tm1_conn,
    effective_rules: Optional[list[str]] = None,
    skip_control_dims: bool = False,
) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    all_dims = tm1_conn.dimensions.get_all_names(skip_control_dims=skip_control_dims)

    _errors: Dict[str, str] = {}
    _dimensions: Dict[str, Dimension] = {}
    skipped_dimensions = 0
    skipped_hierarchies = 0
    skipped_subsets = 0
    logger.info("Exporting %d dimensions", len(all_dims))
    for dim_name in all_dims:
        dim_source_path = os.path.join('dimensions', f"{dim_name}.json").replace('\\', '/')
        if filter_module.should_exclude_path(dim_source_path, effective_rules or []):
            logger.debug("Skipping dimension by filter: %s", dim_source_path)
            skipped_dimensions += 1
            continue

        dim = tm1_conn.dimensions.get(dimension_name=dim_name)
        default_hierarchy = Hierarchy.from_dict(dim.default_hierarchy.body_as_dict, dimension_name=dim_name)

        _dimension = Dimension(name=dim.name, hierarchies=[],
                               defaultHierarchy=default_hierarchy,
                               source_path=dim_source_path)
        _dimensions[dim.name] = _dimension

        for hierarchy in dim.hierarchies:
            hierarchy_source_path = os.path.join('dimensions', f"{dim_name}.hierarchies",
                                                 f"{hierarchy.name}.json").replace('\\', '/')
            if filter_module.should_exclude_path(hierarchy_source_path, effective_rules or []):
                logger.debug("Skipping hierarchy by filter: %s", hierarchy_source_path)
                skipped_hierarchies += 1
                continue

            _hierarchy = Hierarchy(name=hierarchy.name,
                                   elements=[Element(name=v.name, type=v.element_type.value,
                                                     dimension_name=dim_name, hierarchy_name=hierarchy.name)
                                             for k, v in hierarchy.elements.items()],
                                   edges=[Edge(k[0], k[1], v, dimension_name=dim_name, hierarchy_name=hierarchy.name)
                                          for k, v in hierarchy.edges.items()],
                                   subsets=[],
                                   source_path=hierarchy_source_path)

            _dimension.hierarchies.append(_hierarchy)

            if hierarchy.subsets:
                for subset_name in hierarchy.subsets:
                    try:
                        subset = tm1_conn.subsets.get(
                            dimension_name=dim_name, subset_name=subset_name)
                        subset_source_path = os.path.join('dimensions', f"{dim_name}.hierarchies",
                                                          f"{hierarchy.name}.subsets",
                                                          f"{subset_name}.json").replace('\\', '/')
                        if filter_module.should_exclude_path(subset_source_path, effective_rules or []):
                            logger.debug("Skipping subset by filter: %s", subset_source_path)
                            skipped_subsets += 1
                            continue
                        _subset = Subset(name=subset_name,
                                         expression=subset.expression,
                                         source_path=subset_source_path)
                        _hierarchy.subsets.append(_subset)
                    except Exception as e:
                        logger.error(
                            "Failed to export subset '%s' for dimension '%s' hierarchy '%s'",
                            subset_name,
                            dim_name,
                            hierarchy.name,
                            exc_info=True,
                        )
                        _errors[dim_name] = str(e)
    logger.info(
        "Dimension export assembly finished total=%d kept=%d skipped_dimensions=%d skipped_hierarchies=%d skipped_subsets=%d",
        len(all_dims),
        len(_dimensions),
        skipped_dimensions,
        skipped_hierarchies,
        skipped_subsets,
    )
    return _dimensions, _errors


def _parse_rules(rule_text: str, cube_name: str) -> List[Rule]:
    if not rule_text: return []
    rules = []
    seen_names: dict[str, int] = {}

    def _unique_rule_name(area: str) -> str:
        base = Rule.name_from_area(area)
        seen_names[base] = seen_names.get(base, 0) + 1
        if seen_names[base] == 1:
            return base
        return f"{base}_{seen_names[base]}"

    pattern = re.compile(r"(?P<comment>(?:#.*(?:\r\n|\n|$)\s*)*)?(?P<statement>\[.*?\][^;]*;)", re.DOTALL)
    header_match = re.match(r'^(.*?)(?=\[|#|$)', rule_text, re.DOTALL)
    last_pos = 0
    if header_match:
        header_text = header_match.group(1).strip()
        if header_text:
            rules.append(
                Rule(
                    name=_unique_rule_name("[HEADER]"),
                    area="[HEADER]",
                    full_statement=header_text,
                    comment="",
                    cube_name=cube_name
                )
            )
        last_pos = header_match.end()
    for match in pattern.finditer(rule_text, last_pos):
        comment = (match.group('comment') or "").strip()
        statement_text = match.group('statement').strip()
        area_match = re.search(r'(\[.*?\])', statement_text)
        area = area_match.group(1) if area_match else "[UNKNOWN]"
        rules.append(
            Rule(
                name=_unique_rule_name(area),
                area=area,
                full_statement=statement_text,
                comment=comment,
                cube_name=cube_name
            )
        )
    return rules


def server_configs_to_model(tm1_conn: TM1Service) -> Dict:
    configs = tm1_conn.configuration.get_active()
    return configs

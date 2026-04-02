import json
import logging
import os
import re
from typing import Dict, List, Optional
from warnings import catch_warnings
from TM1py import TM1Service
import TM1py

from functools import reduce

from tm1_git_py import filter as filter_module
from tm1_git_py.filter import EntityType, FilterRules
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
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI

from tm1_git_py.tm1py_ext import get_cube_names, get_edges, get_elements, get_subsets, get_process_names, get_views
from tm1_git_py.tm1py_ext.dimension_service_ext import get_names as get_dimension_names
from tm1_git_py.tm1py_ext.hierarchy_service_ext import get_all_names as get_hierarchy_names


logger = logging.getLogger(__name__)


def export(
    tm1_conn: TM1Service,
    filter_rules_list: Optional[list[str]] = None,
    internal_model_dir: Optional[str] = None,
    internal_model_id: Optional[int] = None,
) -> tuple[Model, Dict[str, str]]:
    logger.info("TM1 export started")
    effective_rules = list(filter_rules_list or [])
    filter_rules = FilterRules(effective_rules)
    logger.info(
        "Export filters configured additional_rules=%d effective_rules=%d",
        len(filter_rules_list or []),
        len(effective_rules),
    )

    _dimensions, _dim_errors = dimensions_to_model(
        tm1_conn,
        filter_rules=filter_rules,
        internal_model_dir=internal_model_dir,
        internal_model_id=internal_model_id,
    )

    _cubes, _cube_errors = cubes_to_model(
        tm1_conn,
        _dimensions,
        filter_rules=filter_rules,
    )

    _processes, _process_errors = procs_to_model(
        tm1_conn,
        filter_rules=filter_rules
    )

    _chores, _chore_errors = chores_to_model(tm1_conn, filter_rules=filter_rules)

    _model = Model(cubes=list(_cubes.values()),
                   dimensions=list(_dimensions.values()),
                   processes=list(_processes.values()),
                   chores=list(_chores.values()))
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


def chores_to_model(
    tm1_conn,
    filter_rules: FilterRules,
) -> tuple[Dict[str, Chore], Dict[str, str]]:
    all_chores = tm1_conn.chores.get_all_names()
    _chores: Dict[str, Chore] = {}
    _errors: Dict[str, str] = {}
    skipped_chores = 0
    skipped_tasks = 0
    logger.info("Exporting %d chores", len(all_chores))

    for chore_name in all_chores:
        chore_url = Chore.uri_for(chore_name)
        if filter_rules.should_exclude(chore_url):
            logger.debug("Skipping chore by filter: %s", chore_url)
            skipped_chores += 1
            continue

        chore = tm1_conn.chores.get(chore_name=chore_name)

        tasks_for_model = []
        for tm1py_task in chore.tasks:
            task_dict = tm1py_task.body_as_dict
            process_name = ""
            process_bind = task_dict.get("Process@odata.bind", "")
            match = re.search(r"Processes\('([^']*)'\)", process_bind)
            if match:
                process_name = match.group(1)
            task_path = f"{chore_url}/Tasks('{process_name}')"
            if filter_rules.should_exclude(task_path):
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
    tm1_conn :TM1Service,
    filter_rules: FilterRules,
) -> tuple[Dict[str, Process], Dict[str, str]]:
    processes_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.PROCESS)
    filtered_process_names = [] if processes_tm1_filter.skip_all else get_process_names(
            tm1_conn,
            filter=processes_tm1_filter.filter_expr
    )

    _processes: Dict[str, Process] = {}
    _errors: Dict[str, str] = {}
    logger.info("Exporting %d processes", len(filtered_process_names))
    for process_name in filtered_process_names:
        process = tm1_conn.processes.get(name_process=process_name)

        _ti = TI(prolog_procedure=process.prolog_procedure,
                 metadata_procedure=process.metadata_procedure,
                 data_procedure=process.data_procedure,
                 epilog_procedure=process.epilog_procedure)
        _process = Process(name=process.name, hasSecurityAccess=process.has_security_access,
                           code_link=process_name + '.ti',
                           datasource='',
                           parameters=process.parameters, variables=process.variables, ti=_ti)
        _processes[process.name] = _process
    logger.info(
        "Process export assembly finished total=%d kept=%d",
        len(filtered_process_names),
        len(_processes)
    )
    return _processes, _errors


def cubes_to_model(
    tm1_conn: TM1Service,
    _dimensions: Dict[str, Dimension],
    filter_rules: FilterRules
) -> tuple[Dict[str, Cube], Dict[str, str]]:
    cubes_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.CUBE)
    filtered_cube_names = [] if cubes_tm1_filter.skip_all else get_cube_names(
        tm1_conn,
        filter=cubes_tm1_filter.filter_expr,
    )

    _cubes: Dict[str, Cube] = {}
    _errors: Dict[str, str] = {}
    skipped_rules = 0
    skipped_views = 0
    logger.info("Exporting %d cubes", len(filtered_cube_names))

    for cube_name in filtered_cube_names:
        
        try:
            cube = tm1_conn.cubes.get(cube_name=cube_name)

            rule_text = ""
            if cube.has_rules:
                raw_body = cube.rules.body
                try:
                    rule_data = json.loads(raw_body)
                    rule_text = rule_data.get("Rules", "")
                except (json.JSONDecodeError, AttributeError):
                    rule_text = raw_body if isinstance(raw_body, str) else ""

            rules_list = _parse_rules(rule_text)
            filtered_rules_list = []
            for rule in rules_list:
                rule_path = f"{Rule.uri_for(cube_name)}|{filter_module.normalize_for_path(rule.area)}"
                if filter_rules.should_exclude(rule_path):
                    logger.debug("Skipping rule by filter: %s", rule_path)
                    skipped_rules += 1
                    continue
                filtered_rules_list.append(rule)
            _cube = Cube(
                name=cube_name,
                dimensions=[],
                rules=filtered_rules_list,
                views=[],
            )

            skip_cube_due_to_filtered_dimension = False
            if cube.dimensions:
                for dimension in cube.dimensions:
                    _dimension = _dimensions.get(dimension)
                    if not _dimension:
                        dimension_url = Dimension.uri_for(dimension)
                        if filter_rules.should_exclude(dimension_url):
                            logger.debug(
                                "Skipping cube '%s' because dependent dimension is filtered: %s",
                                cube_name,
                                dimension_url,
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


            _cubes[cube_name] = _cube

            views_tm1_filter = filter_rules.to_tm1_child_name_filter(
                parent_chain=[(EntityType.CUBE, cube_name)],
                child_entity_type=EntityType.VIEW,
            )
            filtered_view_tuples = get_views(
                tm1_conn,
                cube_name=cube_name,
                filter=views_tm1_filter.filter_expr,
            )
            if filtered_view_tuples:
                private_views, public_views = filtered_view_tuples
                for view in private_views + public_views:
                    if isinstance(view, TM1py.Objects.MDXView):
                        _view = MDXView(
                            name=view.name,
                            mdx=view.mdx,
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
                        )
                    else:
                        continue
                    _cube.views.append(_view)


        except Exception as e:
            logger.error("Failed to export cube '%s'", cube_name, exc_info=True)
            _errors[cube_name] = str(e)

    logger.info(
        "Cube export assembly finished total=%d kept=%d skipped_rules=%d skipped_views=%d",
        len(filtered_cube_names),
        len(_cubes),
        skipped_rules,
        skipped_views,
    )
    return _cubes, _errors


def dimensions_to_model(
    tm1_conn: TM1Service,
    filter_rules: FilterRules,
    internal_model_dir: Optional[str] = None,
    internal_model_id: Optional[int] = None,
) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    
    dimensions_tm1_filter = filter_rules.to_tm1_name_filter(EntityType.DIMENSION)
    all_dims = [] if dimensions_tm1_filter.skip_all else get_dimension_names(
        tm1_conn, 
        filter=dimensions_tm1_filter.filter_expr
    )

    _errors: Dict[str, str] = {}
    _dimensions: Dict[str, Dimension] = {}
    model_store = ModelStore.for_main_dir() if internal_model_id is not None else None

    logger.info("Exporting %d dimensions", len(all_dims))

    for dim_name in all_dims:

        try:
            hierarchies_tm1_filter = filter_rules.to_tm1_hierarchy_name_filter(dim_name)
            hierarchy_identities = [] if hierarchies_tm1_filter.skip_all else get_hierarchy_names(
                tm1_conn, dim_name,
                filter=hierarchies_tm1_filter.filter_expr
            )

            hierarchy_list: List[Hierarchy] = []
            for idx, hierarchy_identity in enumerate(hierarchy_identities):
                hierarchy_name = hierarchy_identity.name
                incoming_hierarchy_etag = hierarchy_identity.etag
                elements_tm1_filter = filter_rules.to_tm1_element_name_filter(dim_name, hierarchy_name)
                subsets_tm1_filter = filter_rules.to_tm1_subset_name_filter(dim_name, hierarchy_name)
                edges_tm1_filter = filter_rules.to_tm1_edge_name_filter(dim_name, hierarchy_name)

                can_reuse_elements = False
                can_reuse_subsets = False
                can_reuse_edges = False
                if model_store is not None and incoming_hierarchy_etag is not None:
                    existing_elements_etag, existing_elements_rules = model_store.get_group_reuse_metadata(
                        model_id=internal_model_id,
                        dimension_name=dim_name,
                        hierarchy_name=hierarchy_name,
                        object_type="elements",
                    )
                    existing_subsets_etag, existing_subsets_rules = model_store.get_group_reuse_metadata(
                        model_id=internal_model_id,
                        dimension_name=dim_name,
                        hierarchy_name=hierarchy_name,
                        object_type="subsets",
                    )
                    existing_edges_etag, existing_edges_rules = model_store.get_group_reuse_metadata(
                        model_id=internal_model_id,
                        dimension_name=dim_name,
                        hierarchy_name=hierarchy_name,
                        object_type="edges",
                    )
                    can_reuse_elements = (
                        existing_elements_etag == incoming_hierarchy_etag
                        and existing_elements_rules == elements_tm1_filter.applicable_rules
                    )
                    can_reuse_subsets = (
                        existing_subsets_etag == incoming_hierarchy_etag
                        and existing_subsets_rules == subsets_tm1_filter.applicable_rules
                    )
                    can_reuse_edges = (
                        existing_edges_etag == incoming_hierarchy_etag
                        and existing_edges_rules == edges_tm1_filter.applicable_rules
                    )
                hierarchy = (
                    Hierarchy(
                        name=hierarchy_name,
                        dimension_name=dim_name,
                        internal_model_dir=internal_model_dir,
                        internal_model_id=internal_model_id,
                        hierarchy_etag=incoming_hierarchy_etag,
                        reuse_existing_store=bool(internal_model_dir),
                        elements_filter_rules=elements_tm1_filter.applicable_rules,
                        edges_filter_rules=edges_tm1_filter.applicable_rules,
                        subsets_filter_rules=subsets_tm1_filter.applicable_rules,
                    )
                    if internal_model_dir
                    else Hierarchy(name=hierarchy_name, elements=[], edges=[], subsets=[])
                )

                if can_reuse_elements and can_reuse_subsets and can_reuse_edges:
                    logger.info(
                        "Reusing hierarchy from model store dimension='%s' hierarchy='%s' etag='%s' (elements/subsets/edges unchanged)",
                        dim_name,
                        hierarchy_name,
                        incoming_hierarchy_etag,
                    )
                else:
                    if not can_reuse_elements and hasattr(hierarchy.elements, "replace_with_payloads"):
                        hierarchy.elements.replace_with_payloads(())
                    if not can_reuse_elements and not elements_tm1_filter.skip_all:
                        get_elements(
                            tm1_conn,
                            dim_name,
                            hierarchy_name,
                            filter=elements_tm1_filter.filter_expr,
                            collector=hierarchy.elements,
                        )

                    if not can_reuse_subsets and hasattr(hierarchy.subsets, "replace_with_payloads"):
                        hierarchy.subsets.replace_with_payloads(())
                    if not can_reuse_subsets and not subsets_tm1_filter.skip_all:
                        get_subsets(
                            tm1_conn,
                            dimension_name=dim_name,
                            hierarchy_name=hierarchy_name,
                            filter=subsets_tm1_filter.filter_expr,
                            collector=hierarchy.subsets,
                        )

                    if not can_reuse_edges and hasattr(hierarchy.edges, "replace_with_payloads"):
                        hierarchy.edges.replace_with_payloads(())
                    if not can_reuse_edges and not edges_tm1_filter.skip_all:
                        get_edges(
                            tm1_conn,
                            dim_name,
                            hierarchy_name,
                            filter=edges_tm1_filter.filter_expr,
                            collector=hierarchy.edges,
                        )

                hierarchy.finalize()
                hierarchy_list.append(hierarchy)

            if len(hierarchy_list) > 0:
                _dimension = Dimension(
                    name=dim_name,
                    hierarchies=hierarchy_list,
                    defaultHierarchy=hierarchy_list[0],
                )
            else:
                empty_hierarchy = Hierarchy(name=dim_name, elements=[], edges=[], subsets=[])
                _dimension = Dimension(
                    name=dim_name,
                    hierarchies=[empty_hierarchy],
                    defaultHierarchy=empty_hierarchy,
                )
            _dimensions[dim_name] = _dimension
        except Exception as e:
            logger.error("Failed to export dimension '%s'", dim_name, exc_info=True)
            _errors[dim_name] = str(e)
        
    return _dimensions, _errors


def _parse_rules(rule_text: str) -> List[Rule]:
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
            )
        )
    return rules


def server_configs_to_model(tm1_conn: TM1Service) -> Dict:
    configs = tm1_conn.configuration.get_active()
    return configs

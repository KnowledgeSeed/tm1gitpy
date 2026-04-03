import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
import ijson
import orjson

from tm1_git_py.model import Edge
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.element import Element
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.model.model import Model
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.store_backed_sequence import StoreBackedSequence
from tm1_git_py.model.process import Process
from tm1_git_py.model.rule import Rule
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.task import Task
from tm1_git_py.model.ti import TI


logger = logging.getLogger(__name__)
DESERIALIZE_PROGRESS_EVERY = 100_000


def _json_load_text(raw: str) -> Any:
    return orjson.loads(raw)


def _json_load_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as src:
        return _json_load_text(src.read())


def _subset_sort_tuple(payload: dict) -> tuple[str, str]:
    return (
        str(payload.get("name") or payload.get("Name") or ""),
        str(payload.get("expression") or payload.get("Expression") or ""),
    )


def _element_sort_tuple(payload: dict) -> tuple[str, str]:
    return (
        str(payload.get("Name") or payload.get("name") or ""),
        str(payload.get("Type") or payload.get("type") or ""),
    )


def _edge_sort_tuple(payload: dict) -> tuple[str, str, str]:
    weight = payload.get("Weight")
    if weight is None:
        weight = payload.get("weight")
    return (
        str(payload.get("ParentName") or payload.get("parentName") or payload.get("parent") or ""),
        str(payload.get("ComponentName") or payload.get("componentName") or payload.get("name") or ""),
        str(weight if weight is not None else ""),
    )


def _element_identity_key(payload: dict) -> str:
    return "|".join(_element_sort_tuple(payload))


def _edge_identity_key(payload: dict) -> str:
    return "|".join(_edge_sort_tuple(payload))


def _subset_identity_key(payload: dict) -> str:
    return "|".join(_subset_sort_tuple(payload))


def _recalculate_sequence_signature(sequence: StoreBackedSequence[Any], *, progress_label: str) -> tuple[int, str]:
    row_count = 0
    content_hash = ModelStore.EMPTY_CONTENT_HASH
    for payload_json in sequence.iter_payload_json_strings(
        ordered_by_identity=True,
        progress_label=progress_label,
    ):
        row_count += 1
        content_hash = ModelStore._hash_line(content_hash, (payload_json + "\n").encode("utf-8"))
    sequence.set_content_signature(row_count=row_count, content_hash=content_hash)
    return row_count, content_hash


def _hierarchy_has_top_level_key(hierarchy_json_path: str, key: str) -> bool:
    with open(hierarchy_json_path, "rb") as src:
        for prefix, event, value in ijson.parse(src):
            if prefix == "" and event == "map_key" and value == key:
                return True
    return False


def _iter_hierarchy_array_payloads(hierarchy_json_path: str, key: str) -> Iterator[dict]:
    item_prefix = f"{key}.item"
    emitted = False
    with open(hierarchy_json_path, "rb") as src:
        for payload in ijson.items(src, item_prefix):
            if not isinstance(payload, dict):
                raise ValueError(f"Malformed hierarchy json: non-object payload in array '{key}'")
            emitted = True
            yield payload
    if not emitted and not _hierarchy_has_top_level_key(hierarchy_json_path, key):
        raise ValueError(f"Malformed hierarchy json: key '{key}' not found")


def _append_payloads_in_batches(
    *,
    store: ModelStore,
    group_id: int,
    payloads: Iterable[dict],
    identity_key_fn,
    batch_size: int = 100_000,
    progress_label: Optional[str] = None,
    progress_every: int = DESERIALIZE_PROGRESS_EVERY,
) -> int:
    return store.append_payloads(
        group_id=group_id,
        payloads=payloads,
        identity_key_fn=identity_key_fn,
        batch_size=batch_size,
        progress_label=progress_label,
        progress_every=progress_every,
    )


def _subset_source_mtime_ns(subset_dir_path: str) -> int:
    if not os.path.isdir(subset_dir_path):
        return 0
    latest = 0
    for subset_file_name in os.listdir(subset_dir_path):
        if not subset_file_name.endswith(".json"):
            continue
        subset_path = os.path.join(subset_dir_path, subset_file_name)
        try:
            mtime_ns = int(os.stat(subset_path).st_mtime_ns)
        except OSError:
            continue
        if mtime_ns > latest:
            latest = mtime_ns
    return latest


def _ensure_hierarchy_store_groups(
    *,
    hierarchy_json_path: str,
    model_root: str,
    dimension_name: str,
    hierarchy_name: str,
    subset_dir_path: str,
) -> tuple[StoreBackedSequence[Element], StoreBackedSequence[Edge], StoreBackedSequence[Subset]]:
    store = ModelStore.for_main_dir()
    model_id = store.resolve_model_for_deserialize(model_root)
    elements = StoreBackedSequence.for_elements_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    edges = StoreBackedSequence.for_edges_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    subsets = StoreBackedSequence.for_subsets_sink(
        store=store,
        model_id=model_id,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
    )
    source_mtime_ns = int(os.stat(hierarchy_json_path).st_mtime_ns) if os.path.exists(hierarchy_json_path) else None
    needs_elements_rebuild = source_mtime_ns is None or elements.source_json_mtime_ns() != source_mtime_ns
    needs_edges_rebuild = source_mtime_ns is None or edges.source_json_mtime_ns() != source_mtime_ns
    subset_source_mtime_ns = _subset_source_mtime_ns(subset_dir_path)
    needs_subsets_rebuild = subsets.source_json_mtime_ns() != subset_source_mtime_ns

    if needs_elements_rebuild:
        elements.replace_with_payloads(())
        _append_payloads_in_batches(
            store=store,
            group_id=elements.group_id,
            payloads=_iter_hierarchy_array_payloads(hierarchy_json_path, "Elements"),
            identity_key_fn=_element_identity_key,
            progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:elements",
            progress_every=DESERIALIZE_PROGRESS_EVERY,
        )
        if source_mtime_ns is not None:
            elements.set_source_json_mtime_ns(source_mtime_ns)
        _recalculate_sequence_signature(
            elements,
            progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:elements",
        )

    if needs_edges_rebuild:
        edges.replace_with_payloads(())
        _append_payloads_in_batches(
            store=store,
            group_id=edges.group_id,
            payloads=_iter_hierarchy_array_payloads(hierarchy_json_path, "Edges"),
            identity_key_fn=_edge_identity_key,
            progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:edges",
            progress_every=DESERIALIZE_PROGRESS_EVERY,
        )
        if source_mtime_ns is not None:
            edges.set_source_json_mtime_ns(source_mtime_ns)
        _recalculate_sequence_signature(
            edges,
            progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:edges",
        )

    if needs_subsets_rebuild:
        subsets.replace_with_payloads(())
        if os.path.isdir(subset_dir_path):
            def _subset_payload_iter() -> Iterator[dict]:
                for subset_file_name in sorted(os.listdir(subset_dir_path)):
                    if not subset_file_name.endswith(".json"):
                        continue
                    subset_path = os.path.join(subset_dir_path, subset_file_name)
                    subset_json = _json_load_file(subset_path)
                    yield {
                        "name": subset_json.get("Name") or subset_json.get("name"),
                        "expression": subset_json.get("Expression") or subset_json.get("expression"),
                    }

            _append_payloads_in_batches(
                store=store,
                group_id=subsets.group_id,
                payloads=_subset_payload_iter(),
                identity_key_fn=_subset_identity_key,
                progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:subsets",
                progress_every=DESERIALIZE_PROGRESS_EVERY,
            )
        subsets.set_source_json_mtime_ns(subset_source_mtime_ns)
        _recalculate_sequence_signature(
            subsets,
            progress_label=f"deserialize:{dimension_name}/{hierarchy_name}:subsets",
        )
    return elements, edges, subsets



def _handle_long_path(file_path) -> str:
    file_path = os.path.abspath(file_path)

    if os.name == 'nt' and not file_path.startswith("\\\\?\\"):
        if file_path.startswith("\\\\"):
            file_path = Path(file_path[2:])
            file_path = "\\\\?\\UNC\\" /file_path
            return str(file_path)
        else:
            file_path = Path(file_path)
            file_path = "\\\\?\\" / file_path
            return str(file_path)
    return file_path

def deserialize_model(dir: str) -> tuple[Model, dict[str, str]]:
    logger.info("Deserializing model from '%s'", dir)
    dir = _handle_long_path(dir)

    dimensions_dir = dir + '/dimensions'
    cubes_dir = dir + '/cubes'
    processes_dir = dir + '/processes'
    chores_dir = dir + '/chores'

    _processes, _process_errors = deserialize_processes(processes_dir)

    _chores, _chore_errors = deserialize_chores(chores_dir)

    _dimensions, _dim_errors = deserialize_dimensions(dimensions_dir)

    _cubes, _cube_errors = deserialize_cubes(cubes_dir, _dimensions)

    _model = Model(cubes=list(_cubes.values()),
                   dimensions=list(_dimensions.values()),
                   processes=list(_processes.values()),
                   chores=list(_chores.values()))
    _errors = _dim_errors | _cube_errors | _process_errors | _chore_errors
    logger.info(
        "Deserialized model from '%s' (dimensions=%d cubes=%d processes=%d chores=%d errors=%d)",
        dir,
        len(_dimensions),
        len(_cubes),
        len(_processes),
        len(_chores),
        len(_errors),
    )
    return _model, _errors


def deserialize_chores(chore_dir) -> tuple[Dict[str, Chore], Dict[str, str]]:
    chores: Dict[str, Chore] = {}
    chores_errors: Dict[str, str] = {}
    logger.debug("Deserializing chores from '%s'", chore_dir)
    if not os.path.exists(chore_dir):
        return chores, chores_errors

    for file_name in os.listdir(chore_dir):
        if not file_name.endswith('.json'): continue
        file_name_base, _, _ = file_name.rpartition('.')
        try:
            chore_json = _json_load_file(os.path.join(chore_dir, file_name))

            tasks = []
            for task_data in chore_json.get('Tasks', []):
                process_bind = task_data.get("Process@odata.bind", "")
                match = re.search(r"Processes\('([^']*)'\)", process_bind)
                if match:
                    tasks.append(Task(process_name=match.group(1), parameters=task_data.get('Parameters', [])))

            chores[chore_json['Name']] = Chore(name=chore_json['Name'], start_time=chore_json['StartTime'],
                                               dst_sensitive=chore_json['DSTSensitive'], active=chore_json['Active'],
                                               execution_mode=chore_json['ExecutionMode'],
                                               frequency=chore_json['Frequency'], tasks=tasks)
        except Exception as e:
            chores_link = Chore.uri_for(file_name_base)
            chores_errors[chores_link] = str(e)
            logger.warning("Failed to deserialize chore '%s': %s", file_name, e, exc_info=True)
    return chores, chores_errors


def deserialize_processes(process_dir) -> tuple[Dict[str, Process], Dict[str, str]]:
    processes: Dict[str, Process] = {}
    process_errors: Dict[str, str] = {}
    logger.debug("Deserializing processes from '%s'", process_dir)

    files = directory_to_dict(process_dir)
    for file_name in list(files.keys()):

        file_name_base, dot, file_name_ext = file_name.rpartition('.')
        process_link = Process.uri_for(file_name_base)

        if file_name_ext != 'json' and file_name_ext != 'ti':
            process_errors[process_link] = 'not a process json or ti file'
            logger.warning("Skipping non-process artifact: '%s'", file_name)
            continue
        if file_name_ext != 'json':
            continue

        files.pop(file_name, None)
        process_json = None
        process_ti = None

        with open(os.path.join(process_dir, file_name), 'r', encoding='utf-8') as file:
            try:
                data = file.read()
                process_json = _json_load_text(data)
            except Exception as e:
                process_errors[process_link] = e.__repr__()
                logger.warning("Failed to parse process json '%s': %s", file_name, e, exc_info=True)
                continue

        ti_file_name = file_name_base + '.ti'
        if ti_file_name not in files:
            process_errors[process_link] = 'related ti not found at ' + Process.uri_for(file_name_base)
            logger.warning("Missing TI pair for process json '%s'", file_name)
            continue

        with open(os.path.join(process_dir, ti_file_name), 'r', encoding='utf-8') as file:
            try:
                data = file.read()
                process_ti = TI.from_string(data)
            except Exception as e:
                process_errors[process_link] = e.__repr__()
                logger.warning("Failed to parse process TI '%s': %s", ti_file_name, e, exc_info=True)
            finally:
                files.pop(ti_file_name, None)

        try:
            _process = Process(
                name=process_json['Name'],
                hasSecurityAccess=process_json['HasSecurityAccess'],
                code_link=process_json['Code@Code.link'],
                datasource=None,  # datasource=process_json.get('DataSource'), ?
                parameters=process_json['Parameters'],
                variables=process_json['Variables'],
                ti=process_ti,
            )
            processes[process_json['Name']] = _process
        except Exception as e:
            process_errors[process_link] = e.__repr__()
            logger.warning("Failed to build process object for '%s': %s", file_name, e, exc_info=True)

    return processes, process_errors


def deserialize_dimensions(dimension_dir) -> tuple[Dict[str, Dimension], Dict[str, str]]:
    dimensions: Dict[str, Dimension] = {}
    dimension_errors: Dict[str, str] = {}
    logger.debug("Deserializing dimensions from '%s'", dimension_dir)

    files = directory_to_dict(dimension_dir)
    for file_name in list(files.keys()):
        file_name_base, dot, file_name_ext = file_name.rpartition('.')
        dim_link = Dimension.uri_for(file_name_base)

        if file_name_ext not in ['json', 'hierarchies']:
            dimension_errors[dim_link] = 'not a dimension json or .hierarchies folder'
            logger.warning("Skipping non-dimension artifact: '%s'", file_name)
            continue
        if file_name_ext != 'json':
            continue

        files.pop(file_name, None)
        dim_json = None

        with open(os.path.join(dimension_dir, file_name), 'r', encoding='utf-8') as file:
            try:
                data = file.read()
                dim_json = _json_load_text(data)
            except Exception as e:
                dimension_errors[dim_link] = e.__repr__()
                logger.warning("Failed to parse dimension json '%s': %s", file_name, e, exc_info=True)
                continue

        try:
            dim_name = dim_json['Name']
            _dimension = Dimension(name=dim_name, hierarchies=[], defaultHierarchy=None)
        except Exception as e:
            dimension_errors[dim_link] = e.__repr__()
            logger.warning("Failed to build dimension object for '%s': %s", file_name, e, exc_info=True)
            continue

        hier_dir_name = file_name_base + '.hierarchies'
        hier_dir_path = os.path.join(dimension_dir, hier_dir_name)

        if hier_dir_name not in files and not os.path.isdir(hier_dir_path):
            dimension_errors[dim_link] = 'no hierarchies found'
            logger.warning("No hierarchy directory found for dimension '%s'", file_name)
            continue

        hiers = files.get(hier_dir_name)
        for hier_file_name in list(hiers.keys()):
            # Ignore temporary/in-progress hierarchy artifacts.
            if hier_file_name.endswith(".json.inprogress") or hier_file_name.endswith(".tmp.json.meta.json"):
                continue
            hier_file_name_base, dot, file_name_ext = hier_file_name.rpartition('.')
            hier_link = Hierarchy.uri_for(file_name_base, hier_file_name_base)

            if file_name_ext not in ['json', 'subsets']:
                dimension_errors[hier_link] = 'not a hierarchy json or .subset folder'
                logger.warning("Skipping non-hierarchy artifact: '%s'", hier_file_name)
                continue
            if file_name_ext != 'json':
                continue

            hiers.pop(hier_file_name, None)

            hierarchy_json_path = os.path.join(hier_dir_path, hier_file_name)
            try:
                subset_dir_name = hier_file_name_base + '.subsets'
                subset_dir_path = os.path.join(hier_dir_path, subset_dir_name)
                model_root = os.path.dirname(dimension_dir)
                elements, edges, subsets = _ensure_hierarchy_store_groups(
                    hierarchy_json_path=hierarchy_json_path,
                    model_root=model_root,
                    dimension_name=file_name_base,
                    hierarchy_name=hier_file_name_base,
                    subset_dir_path=subset_dir_path,
                )

                _hierarchy = Hierarchy(
                    name=hier_file_name_base,
                    elements=elements,
                    edges=edges,
                    subsets=subsets,
                )

                _dimension.hierarchies.append(_hierarchy)
                pattern = r"Dimensions\('([^']*)'\)/Hierarchies\('([^']*)'\)"
                match = re.search(pattern, dim_json['DefaultHierarchy'])
                if match:
                    _, hierarchy = match.groups()
                    if hierarchy == hier_file_name_base:
                        _dimension.defaultHierarchy = _hierarchy
            except Exception as e:
                dimension_errors[hier_link] = e.__repr__()
                logger.warning(
                    "Failed to parse/build hierarchy '%s' for dimension '%s': %s",
                    hier_file_name,
                    file_name,
                    e,
                    exc_info=True,
                )

        if not _dimension.defaultHierarchy:
            dimension_errors[dim_link] = 'no default hierarchy'
            logger.warning("No default hierarchy resolved for dimension '%s'", file_name)
            continue
        dimensions[_dimension.name] = _dimension
    return dimensions, dimension_errors


def deserialize_cubes(cubes_dir, _dimensions: Dict[str, Dimension]) -> tuple[Dict[str, Cube], Dict[str, str]]:
    cubes: Dict[str, Cube] = {}
    cube_errors: Dict[str, str] = {}
    logger.debug("Deserializing cubes from '%s'", cubes_dir)

    files = directory_to_dict(cubes_dir)
    for file_name in list(files.keys()):
        file_name_base, dot, file_name_ext = file_name.rpartition('.')
        cube_link = Cube.uri_for(file_name_base)

        if file_name_ext not in ['json', 'rules', 'views']:
            cube_errors[cube_link] = 'not a dimension json or .rules or .views folder'
            logger.warning("Skipping non-cube artifact: '%s'", file_name)
            continue
        if file_name_ext != 'json':
            continue

        files.pop(file_name, None)
        cube_json = None

        with open(os.path.join(cubes_dir, file_name), 'r', encoding='utf-8') as file:
            cube_json = _json_load_text(file.read())
            rules_list = []
            rule_file_path = os.path.join(cubes_dir, file_name_base + '.rules')
            if os.path.exists(rule_file_path):
                with open(rule_file_path, 'r', encoding='utf-8') as file:
                    rule_text = file.read()
                    rules_list = _parse_rules(rule_text, cube_name=file_name_base)
            _cube = Cube(name=cube_json['Name'], dimensions=[], rules=rules_list, views=[])

        for dim in cube_json['Dimensions']:
            pattern = r"Dimensions\('([^']*)'\)"
            match = re.search(pattern, dim['@id'])
            if match:
                dimension = match.groups()
                _dimension = _dimensions.get(dimension[0])
                if _dimension:
                    _cube.dimensions.append(_dimension)

        view_dir_name = file_name_base + '.views'
        view_dir_path = os.path.join(cubes_dir, view_dir_name)
        if view_dir_name in files and os.path.isdir(view_dir_path):
            views = files.get(view_dir_name)
            for view_file_name in list(views.keys()):
                view_file_name_base, dot, file_name_ext = view_file_name.rpartition('.')

                view = None
                mdx = None
                if file_name_ext == 'json':
                    with open(os.path.join(view_dir_path, view_file_name), 'r', encoding='utf-8') as file:
                        try:
                            data = file.read()
                            view = _json_load_text(data)
                        except Exception as e:
                            cube_errors[file_name_base + '.views/' + view_file_name] = e.__repr__()
                            logger.warning(
                                "Failed to parse view '%s' for cube '%s': %s",
                                view_file_name,
                                file_name_base,
                                e,
                                exc_info=True,
                            )
                else:
                    continue

                view_type = (view.get('@type') or '').lower()

                if view_type == 'mdxview':
                    mdx_file_name = view_file_name_base + '.mdx'
                    if mdx_file_name in views:
                        with open(os.path.join(view_dir_path, mdx_file_name), 'r', encoding='utf-8') as file:
                            try:
                                mdx = file.read()
                            except Exception as e:
                                cube_errors[file_name_base + '.mdx'] = e.__repr__()
                                logger.warning(
                                    "Failed to parse mdx '%s' for cube '%s': %s",
                                    mdx_file_name,
                                    file_name_base,
                                    e,
                                    exc_info=True,
                                )
                        files.pop(mdx_file_name, None)
                    else:
                        cube_errors[mdx_file_name] = 'mdx not found'
                        continue

                    if not mdx:
                        cube_errors[mdx_file_name] = 'mdx cannot be parsed'
                        continue

                    _cube.views.append(MDXView(name=view['Name'], mdx=mdx))
                elif view_type == 'nativeview':
                    _cube.views.append(
                        NativeView(
                            name=view['Name'],
                            columns=view.get('Columns', []),
                            rows=view.get('Rows', []),
                            titles=view.get('Titles', []),
                            suppress_empty_columns=view.get('SuppressEmptyColumns', False),
                            suppress_empty_rows=view.get('SuppressEmptyRows', False),
                            format_string=view.get('FormatString', '0.#########'),
                        )
                    )
                else:
                    cube_errors[file_name_base + '.views/' + view_file_name] = "unsupported view type"
                    logger.warning(
                        "Unsupported view type for '%s' in cube '%s'",
                        view_file_name,
                        file_name_base,
                    )
        cubes[_cube.name] = _cube
    return cubes, cube_errors


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


def directory_to_dict(path):
    """Converts a directory structure to a nested dictionary."""
    if not os.path.isdir(path):
        logger.debug("Directory '%s' not found, returning empty structure", path)
        return {}
    directory_dict = {}
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        if os.path.isdir(item_path):
            # If the item is a directory, recursively populate its contents
            directory_dict[item] = directory_to_dict(item_path)
        else:
            # If the item is a file, set it to None or any specific value if needed
            directory_dict[item] = None
    return directory_dict

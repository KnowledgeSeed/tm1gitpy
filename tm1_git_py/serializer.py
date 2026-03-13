import logging
import os
from pathlib import Path
from typing import List

from tm1_git_py.model.chore import Chore
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.model import Model
from tm1_git_py.model.process import Process


logger = logging.getLogger(__name__)


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


def serialize_model(model: Model, dir):
    logger.info(
        "Serializing model to '%s' (dimensions=%d cubes=%d processes=%d chores=%d)",
        dir,
        len(model.dimensions),
        len(model.cubes),
        len(model.processes),
        len(model.chores),
    )
    os.makedirs(dir, exist_ok=True)

    dir = _handle_long_path(dir)

    dim_dir = dir + '/dimensions'
    if (model.dimensions):
        os.makedirs(dim_dir, exist_ok=True)
    serialize_dimensions(model.dimensions, dim_dir)

    cubes_dir = dir + '/cubes'
    if (model.cubes):
        os.makedirs(cubes_dir, exist_ok=True)
    serialize_cubes(model.cubes, cubes_dir)

    processes_dir = dir + '/processes'
    if (model.processes):
        os.makedirs(processes_dir, exist_ok=True)
    serialize_processes(model.processes, processes_dir)

    chores_dir = dir + '/chores'
    if (model.chores):
        os.makedirs(chores_dir, exist_ok=True)
    serialize_chores(model.chores, chores_dir)
    logger.info("Model serialization finished for '%s'", dir)


def serialize_dimensions(dimensions: List[Dimension], dim_dir):
    logger.debug("Serializing %d dimension(s) into '%s'", len(dimensions), dim_dir)
    for dim in dimensions:
        for _hierarchy in dim.hierarchies:
            hierarchy_dir = dim_dir + '/' + dim.name + '.hierarchies'
            os.makedirs(hierarchy_dir, exist_ok=True)
            for _subset in _hierarchy.subsets:
                subsets_dir = hierarchy_dir + '/' + _hierarchy.name + '.subsets'
                os.makedirs(subsets_dir, exist_ok=True)
                with open(subsets_dir + '/' + _subset.name + '.json', 'w', encoding='utf-8') as subset_file:
                    subset_file.write(_subset.as_json())
            with open(hierarchy_dir + '/' + _hierarchy.name + '.json', 'w', encoding='utf-8') as hierarchy_file:
                hierarchy_file.write(_hierarchy.as_json())
        with open(dim_dir + '/' + dim.name + '.json', 'w', encoding='utf-8') as dim_file:
            dim_file.write(dim.as_json())


def serialize_cubes(cubes: List[Cube], cubes_dir):
    logger.debug("Serializing %d cube(s) into '%s'", len(cubes), cubes_dir)
    for cube in cubes:
        if cube.rules:
            rule_text = cube.get_rule_text()
            if rule_text:
                with open(os.path.join(cubes_dir, cube.name + '.rules'), 'w', encoding='utf-8') as rule_file:
                    rule_file.write(rule_text)

        with open(os.path.join(cubes_dir, cube.name + '.json'), 'w', encoding='utf-8') as cube_file:
            cube_file.write(cube.as_json())

        if cube.views:
            views_dir = os.path.join(cubes_dir, cube.name + '.views')
            os.makedirs(views_dir, exist_ok=True)
            for view in cube.views:
                view_type = getattr(view, 'type', '').lower()
                with open(os.path.join(views_dir, view.name + '.json'), 'w', encoding='utf-8') as view_json_file:
                    view_json_file.write(view.as_json())
                if view_type == 'mdxview':
                    with open(os.path.join(views_dir, view.name + '.mdx'), 'w', encoding='utf-8') as mdx_file:
                        mdx_file.write(view.mdx)

def serialize_processes(processes: List[Process], process_dir):
    logger.debug("Serializing %d process(es) into '%s'", len(processes), process_dir)
    for process in processes:
        with open(process_dir + '/' + process.name + '.ti', 'w', encoding='utf-8', newline='\n') as processti_file:
            processti_file.write(process.ti.ti_as_string())

        with open(process_dir + '/' + process.name + '.json', 'w', encoding='utf-8') as processjson_file:
            processjson_file.write(process.as_json())


def serialize_chores(chores: List[Chore], chores_dir):
    logger.debug("Serializing %d chore(s) into '%s'", len(chores), chores_dir)
    for chore in chores:
        with open(chores_dir + '/' + chore.name + '.json', 'w', encoding='utf-8') as chore_file:
            chore_file.write(chore.as_json())

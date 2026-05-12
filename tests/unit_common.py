import json
import os.path
import shutil
import sqlite3
import types
from concurrent.futures import ProcessPoolExecutor
from unittest import mock
from pathlib import Path
from typing import TypeVar

import pytest
import yaml

import tm1_git_py.services.comparator
import tm1_git_py.services.deserializer as deserializer_module
import tm1_git_py.services.exporter as exporter_module
import tm1_git_py.main as main_module
from tests.utility import (
    _build_mock_changeset_data,
    _objects_equal_case_builders,
    build_mock_model,
    dim_data,
    chore_data,
    process_data,
    make_dimension, make_subset, make_chore, make_process, make_mdx_view, make_cube, make_rule, make_hierarchy,
    make_element
)
from tm1_git_py.services.apply import apply, create_object
from tm1_git_py.services.exporter import export
from tm1_git_py.services.serializer import serialize_model, serialize_dimensions
from tm1_git_py.services.comparator import Comparator
from tm1_git_py.services.changeset import Change, ChangeType, Changeset, ObjectType, import_changeset
from tm1_git_py.services.filter import (
    DEFAULT_TM1_TECHNICAL_OBJECTS,
    EntityType,
    FilterRules,
    filter_changeset,
    normalize_for_path,
    with_technical_objects_ignore,
    should_exclude_path,
    with_default_leaves_ignore,
)
from tm1_git_py.services.deserializer import *
from tm1_git_py.model import *
from tm1_git_py.model import dimension, hierarchy, subset, chore, process, cube, mdxview, edge, element
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.reporting.progress_reporting import (
    CallbackProgressSink,
    ProgressEvent,
    ProgressKind,
    ProgressScope,
    ProgressUnit,
    TqdmProgressSink,
)
from tm1_git_py.tm1_api import subset_service, process_service, cube_service, view_service

T = TypeVar('T', Cube, Dimension, Process, Chore)


TEST_ROOT = Path(__file__).resolve().parent
test_model_dir_base = TEST_ROOT / "model_test_export" / "test_model_base"
test_model_dir_diff = TEST_ROOT / "model_test_export" / "test_model_diff"


@pytest.fixture(params=list(_objects_equal_case_builders().keys()), ids=list(_objects_equal_case_builders().keys()))
def objects_equal_data(request):
    builders = _objects_equal_case_builders()
    return builders[request.param]()


__all__ = [name for name in globals() if not name.startswith("__")]

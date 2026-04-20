import json
import os.path
import shutil
import sqlite3
import types
from unittest import mock
from pathlib import Path
from typing import TypeVar

import pytest
import yaml

import tm1_git_py.comparator
import tm1_git_py.deserializer as deserializer_module
import tm1_git_py.exporter as exporter_module
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
from tm1_git_py.apply import apply
from tm1_git_py.exporter import export
from tm1_git_py.serializer import serialize_model, serialize_dimensions
from tm1_git_py.comparator import Comparator
from tm1_git_py.changeset import Change, ChangeType, Changeset, ObjectType, import_changeset
from tm1_git_py.filter import (
    EntityType,
    FilterRules,
    filter_changeset,
    should_exclude_path,
    with_default_leaves_ignore,
)
from tm1_git_py import filter as filter_module
from tm1_git_py.deserializer import *
from tm1_git_py.model import *
from tm1_git_py.model import dimension, hierarchy, subset, chore, process, cube, mdxview, edge, element
from tm1_git_py.model.nativeview import NativeView
from tm1_git_py.progress_reporting import (
    CallbackProgressSink,
    ProgressEvent,
    ProgressKind,
    ProgressScope,
    ProgressUnit,
    TqdmProgressSink,
)
from tm1_git_py.tm1py_ext import subset_service_ext, process_service_ext, cube_service_ext, view_service_ext

T = TypeVar('T', Cube, Dimension, Process, Chore)


TEST_ROOT = Path(__file__).resolve().parent
test_model_dir_base = TEST_ROOT / "model_test_export" / "test_model_base"
test_model_dir_diff = TEST_ROOT / "model_test_export" / "test_model_diff"


@pytest.fixture(params=list(_objects_equal_case_builders().keys()), ids=list(_objects_equal_case_builders().keys()))
def objects_equal_data(request):
    builders = _objects_equal_case_builders()
    return builders[request.param]()



class TestDeserializer:

    def test_deserialize_chores(self, chores_dir=test_model_dir_base / 'chores'):
        chores, errors = deserialize_chores(chore_dir=chores_dir)
        for chore in chores.values():
            assert isinstance(chore, Chore)


    def test_deserialize_dimensions(self, dimensions_dir=test_model_dir_base / 'dimensions'):
        dimensions, errors = deserialize_dimensions(dimensions_dir, test_model_dir_base.name)
        for dimension in dimensions.values():
            assert isinstance(dimension, Dimension)

    def test_deserialize_model_forwards_max_workers(self, monkeypatch, tmp_path):
        monkeypatch.setattr(deserializer_module, "deserialize_processes", lambda *_a, **_k: ({}, {}))
        monkeypatch.setattr(deserializer_module, "deserialize_chores", lambda *_a, **_k: ({}, {}))
        monkeypatch.setattr(deserializer_module, "deserialize_cubes", lambda *_a, **_k: ({}, {}))
        observed: dict[str, int] = {}

        def _fake_dimensions(*_args, **kwargs):
            observed["max_workers"] = int(kwargs.get("max_workers"))
            return {}, {}

        monkeypatch.setattr(deserializer_module, "deserialize_dimensions", _fake_dimensions)

        model, errors = deserializer_module.deserialize_model(str(tmp_path), max_workers=7)
        assert observed["max_workers"] == 7
        assert model.total_object_count == 0
        assert errors == {}

    def test_tqdm_deserializer_sink_uses_dynamic_worker_slots(self, tmp_path):
        sink = TqdmProgressSink(worker_count=6)
        try:
            assert sink.worker_count == 6
            assert len(sink._worker_bars) == 6
        finally:
            sink.close()

    def test_tqdm_deserializer_sink_extends_generic_sink(self, tmp_path):
        sink = TqdmProgressSink(worker_count=3)
        try:
            assert isinstance(sink, TqdmProgressSink)
            assert sink.worker_count == 3
            assert len(sink._worker_bars) == 3
        finally:
            sink.close()


    def test_deserialize_dimension_with_children(self, dimensions_dir=test_model_dir_base / 'dimensions'):
        dimensions, errors = deserialize_dimensions(dimensions_dir, test_model_dir_base.name)
        dim_version = dimensions.get('testbenchVersion')
        hier_version = dim_version.hierarchies[0]
        assert dim_version.name == 'testbenchVersion'
        assert hier_version.name == 'testbenchVersion'
        assert hier_version.elements[0].to_dict() == {"Name": "Actual", "Type": "Numeric"}

    def test_hierarchy_constructor_with_model_id_creates_store_backed_refs(self, tmp_path):
        model_id = f"{tmp_path.name}_internal"
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=model_id,
        )
        assert isinstance(hierarchy_obj.elements, StoreBackedSequence)
        assert isinstance(hierarchy_obj.edges, StoreBackedSequence)
        assert isinstance(hierarchy_obj.subsets, StoreBackedSequence)

    def test_hierarchy_constructor_without_model_id_uses_provided_refs(self):
        elements = [Element(name="E1", type="Numeric")]
        edges = [Edge(parent="P", component_name="C", weight=1)]
        subsets = [Subset(name="S1", expression="{E1}")]
        hierarchy_obj = Hierarchy(name="MyHier", elements=elements, edges=edges, subsets=subsets)
        assert hierarchy_obj.elements is elements
        assert hierarchy_obj.edges is edges
        assert hierarchy_obj.subsets is subsets

    def test_hierarchy_as_json_in_memory_collections_remains_parseable(self):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            elements=[Element(name="E1", type="Numeric")],
            edges=[Edge(parent="P", component_name="E1", weight=1)],
            subsets=[Subset(name="S1", expression="{E1}")],
        )

        payload = json.loads(hierarchy_obj.as_json())
        assert payload["@type"] == "Hierarchy"
        assert payload["Name"] == "MyHier"
        assert payload["Elements"] == [{"Name": "E1", "Type": "Numeric"}]
        assert payload["Edges"] == [{"ParentName": "P", "ComponentName": "E1", "Weight": 1}]
        assert payload["Subsets@Code.links"] == ["MyHier.subsets/S1.json"]

    def test_hierarchy_as_json_streams_store_backed_collections(self, tmp_path):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=tmp_path.name,
        )
        hierarchy_obj.elements.extend(
            [
                Element(name="E1", type="Numeric"),
                Element(name="E2", type="String"),
            ]
        )
        hierarchy_obj.edges.extend(
            [
                Edge(parent="E1", component_name="E2", weight=1),
            ]
        )
        hierarchy_obj.subsets.extend(
            [
                Subset(name="S1", expression="{E1}"),
            ]
        )

        payload = json.loads(hierarchy_obj.as_json())
        assert payload["Elements"] == [
            {"Name": "E1", "Type": "Numeric"},
            {"Name": "E2", "Type": "String"},
        ]
        assert payload["Edges"] == [
            {"ParentName": "E1", "ComponentName": "E2", "Weight": 1},
        ]
        assert payload["Subsets@Code.links"] == ["MyHier.subsets/S1.json"]

    def test_hierarchy_finalize_sorts_store_backed_groups(self, tmp_path):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=tmp_path.name,
        )
        hierarchy_obj.elements.extend(
            [
                Element(name="C", type="Numeric"),
                Element(name="A", type="String"),
                Element(name="A", type="Numeric"),
            ]
        )
        hierarchy_obj.edges.extend(
            [
                Edge(parent="Root", component_name="B", weight=2),
                Edge(parent="Root", component_name="A", weight=1),
            ]
        )
        hierarchy_obj.subsets.extend(
            [
                Subset(name="Subset_B", expression="{B}"),
                Subset(name="Subset_A", expression="{A}"),
            ]
        )

        payload = json.loads(hierarchy_obj.as_json())
        assert [(e["Name"], e["Type"]) for e in payload["Elements"]] == [
            ("A", "Numeric"),
            ("A", "String"),
            ("C", "Numeric"),
        ]
        assert [(e["ParentName"], e["ComponentName"], e["Weight"]) for e in payload["Edges"]] == [
            ("Root", "B", 2),
            ("Root", "A", 1),
        ]
        assert payload["Subsets@Code.links"] == [
            "MyHier.subsets/Subset_B.json",
            "MyHier.subsets/Subset_A.json",
        ]

        assert hierarchy_obj.elements.source_json_mtime_ns() is None
        assert hierarchy_obj.edges.source_json_mtime_ns() is None
        assert hierarchy_obj.subsets.source_json_mtime_ns() is None

    def test_hierarchy_staged_writer_overwrites_partial_content(self, tmp_path):
        model_id = tmp_path.name

        first = Hierarchy(name="MyHier", dimension_name="MyDim", model_id=model_id)
        first.elements.extend([Element(name="Old", type="Numeric")])
        # Simulate interrupted export by not finalizing/writing.

        second = Hierarchy(name="MyHier", dimension_name="MyDim", model_id=model_id)
        second.elements.extend([Element(name="New", type="Numeric")])
        payload = json.loads(second.as_json())

        assert payload["Elements"] == [{"Name": "New", "Type": "Numeric"}]

    def test_serialize_dimensions_uses_hierarchy_write_json(self, tmp_path, monkeypatch):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            elements=[Element(name="E1", type="Numeric")],
            edges=[],
            subsets=[],
        )
        dimension_obj = Dimension(name="MyDim", hierarchies=[hierarchy_obj], defaultHierarchy=hierarchy_obj)
        monkeypatch.setattr(Hierarchy, "as_json", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("as_json should not be used")))

        serialize_dimensions([dimension_obj], str(tmp_path))

        hierarchy_file = tmp_path / "MyDim.hierarchies" / "MyHier.json"
        assert hierarchy_file.exists()
        payload = json.loads(hierarchy_file.read_text(encoding="utf-8"))
        assert payload["Name"] == "MyHier"

    def test_serialize_dimensions_writes_default_hierarchy_as_id_object(self, tmp_path):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            elements=[Element(name="E1", type="Numeric")],
            edges=[],
            subsets=[],
        )
        dimension_obj = Dimension(
            name="MyDim",
            hierarchies=[hierarchy_obj],
            defaultHierarchy=hierarchy_obj,
        )

        serialize_dimensions([dimension_obj], str(tmp_path))

        dimension_file = tmp_path / "MyDim.json"
        payload = json.loads(dimension_file.read_text(encoding="utf-8"))
        assert payload["DefaultHierarchy"] == {
            "@id": "Dimensions('MyDim')/Hierarchies('MyHier')"
        }

    def test_serialize_dimensions_writes_model_id_backed_hierarchy(self, tmp_path):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=tmp_path.name,
        )
        hierarchy_obj.elements.extend([Element(name="E1", type="Numeric")])
        dimension_obj = Dimension(name="MyDim", hierarchies=[hierarchy_obj], defaultHierarchy=hierarchy_obj)

        dim_dir = tmp_path / "dimensions"
        dim_dir.mkdir(exist_ok=True)
        serialize_dimensions([dimension_obj], str(dim_dir))

        hierarchy_file = dim_dir / "MyDim.hierarchies" / "MyHier.json"
        assert hierarchy_file.exists()
        payload = json.loads(hierarchy_file.read_text(encoding="utf-8"))
        assert payload["Elements"] == [{"Name": "E1", "Type": "Numeric"}]

    def test_serialize_dimensions_skips_rewrite_when_hierarchy_staged_writer_enabled(self, tmp_path, monkeypatch):
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=tmp_path.name,
            model_output_dir=str(tmp_path),
            serialize=True,
        )
        hierarchy_obj.elements.extend([Element(name="E1", type="Numeric")])
        dimension_obj = Dimension(name="MyDim", hierarchies=[hierarchy_obj], defaultHierarchy=hierarchy_obj)

        monkeypatch.setattr(
            Hierarchy,
            "write_json",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("write_json should be skipped")
            ),
        )

        dim_dir = tmp_path / "dimensions"
        dim_dir.mkdir(exist_ok=True)
        serialize_dimensions([dimension_obj], str(dim_dir))

        hierarchy_file = dim_dir / "MyDim.hierarchies" / "MyHier.json"
        assert hierarchy_file.exists()
        payload = json.loads(hierarchy_file.read_text(encoding="utf-8"))
        assert payload["Elements"] == [{"Name": "E1", "Type": "Numeric"}]

    def test_hierarchy_staged_writer_falls_back_to_model_id_folder(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        model_id = "fallback_model"
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=model_id,
            serialize=True,
        )
        hierarchy_obj.elements.extend([Element(name="E1", type="Numeric")])
        dimension_obj = Dimension(name="MyDim", hierarchies=[hierarchy_obj], defaultHierarchy=hierarchy_obj)

        dim_dir = tmp_path / "dimensions"
        dim_dir.mkdir(exist_ok=True)
        serialize_dimensions([dimension_obj], str(dim_dir))

        staged_hierarchy_file = (
            tmp_path / model_id / "dimensions" / "MyDim.hierarchies" / "MyHier.json"
        )
        assert staged_hierarchy_file.exists()
        payload = json.loads(staged_hierarchy_file.read_text(encoding="utf-8"))
        assert payload["Elements"] == [{"Name": "E1", "Type": "Numeric"}]

        canonical_hierarchy_file = dim_dir / "MyDim.hierarchies" / "MyHier.json"
        assert canonical_hierarchy_file.exists()
        assert json.loads(canonical_hierarchy_file.read_text(encoding="utf-8"))["Elements"] == [
            {"Name": "E1", "Type": "Numeric"}
        ]

    def test_deserialize_dimensions_ignores_inprogress_hierarchy_files(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        hier_dir = dimensions_dir / "testbenchVersion.hierarchies"
        inprogress = hier_dir / ".testbenchVersion.json.inprogress"
        inprogress.write_text('{"Name":"broken"', encoding="utf-8")

        dimensions, errors = deserialize_dimensions(dimensions_dir, tmp_path.name)
        assert "testbenchVersion" in dimensions
        assert not any("inprogress" in key for key in errors.keys())

    def test_deserialize_dimensions_accepts_default_hierarchy_id_object(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        dimension_file = dimensions_dir / "testbenchVersion.json"
        payload = json.loads(dimension_file.read_text(encoding="utf-8"))
        payload["DefaultHierarchy"] = {
            "@id": "Dimensions('testbenchVersion')/Hierarchies('testbenchVersion')"
        }
        dimension_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        dimensions, errors = deserialize_dimensions(dimensions_dir, tmp_path.name)
        assert "testbenchVersion" in dimensions
        assert dimensions["testbenchVersion"].defaultHierarchy.name == "testbenchVersion"
        assert (
            "Dimensions('testbenchVersion')" not in errors
            and "Dimensions('testbenchVersion')/Hierarchies('testbenchVersion')"
            not in errors
        )

    def test_get_subsets_uses_collector(self, monkeypatch):
        tm1_conn = mock.Mock()
        collector: list[Subset] = []

        sequence = iter([
                subset_service_ext.PaginatedSubsetsResult(
                    subsets=[Subset(name="S1", expression="{A}")], count=2, skip=0, top=1
                ),
                subset_service_ext.PaginatedSubsetsResult(
                    subsets=[Subset(name="S2", expression="{B}")], count=None, skip=1, top=1
                ),
            ]
        )
        monkeypatch.setattr(subset_service_ext, "_get_subsets_page", lambda *args, **kwargs: next(sequence))

        result = subset_service_ext.get_subsets(
            tm1_conn,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
            page_size=1,
            collector=collector,
        )

        assert result is collector
        assert [s.name for s in collector] == ["S1", "S2"]

    def test_deserialize_dimensions_uses_store_backed_hierarchy_collections(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        dimensions, _ = deserialize_dimensions(dimensions_dir, tmp_path.name)
        dim_version = dimensions.get("testbenchVersion")
        assert dim_version is not None
        hier_version = dim_version.hierarchies[0]
        assert isinstance(hier_version.elements, StoreBackedSequence)
        assert isinstance(hier_version.edges, StoreBackedSequence)
        assert isinstance(hier_version.subsets, StoreBackedSequence)

    def test_deserialize_dimensions_creates_sqlite_internal_store(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        deserialize_dimensions(dimensions_dir, tmp_path.name)
        deserialize_dimensions(dimensions_dir, tmp_path.name)
        assert (Path.cwd() / ".tm1gitpy" / "model_store.sqlite").exists()

    def test_deserialize_dimensions_populates_store_group_metadata(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        dimensions, _ = deserialize_dimensions(dimensions_dir, tmp_path.name)
        hierarchy_obj = dimensions["testbenchVersion"].hierarchies[0]
        assert hierarchy_obj.elements.sidecar_content_signature()[0] == len(hierarchy_obj.elements)
        assert hierarchy_obj.edges.sidecar_content_signature()[0] == len(hierarchy_obj.edges)
        assert hierarchy_obj.subsets.sidecar_content_signature()[0] == len(hierarchy_obj.subsets)

    def test_deserialize_rebuilds_store_groups_when_source_hierarchy_is_newer(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        deserialize_dimensions(dimensions_dir, tmp_path.name)

        hierarchy_json = dimensions_dir / "testbenchVersion.hierarchies" / "testbenchVersion.json"
        with open(hierarchy_json, "r+", encoding="utf-8") as fh:
            payload = json.load(fh)
            payload["Name"] = payload.get("Name", "testbenchVersion")
            fh.seek(0)
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
            fh.truncate()

        dimensions_after, _ = deserialize_dimensions(dimensions_dir, tmp_path.name)
        after_hier = dimensions_after["testbenchVersion"].hierarchies[0]
        assert after_hier.elements.source_json_mtime_ns() == int(hierarchy_json.stat().st_mtime_ns)

    def test_deserialize_uses_store_group_builder(self, tmp_path, monkeypatch):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        calls = {"group_builder": 0}

        import tm1_git_py.deserializer as deserializer_module

        original_builder = deserializer_module._ensure_hierarchy_store_groups

        def _builder_spy(*args, **kwargs):
            calls["group_builder"] += 1
            return original_builder(*args, **kwargs)

        monkeypatch.setattr(deserializer_module, "_ensure_hierarchy_store_groups", _builder_spy)

        deserialize_dimensions(dimensions_dir, tmp_path.name)
        assert calls["group_builder"] >= 1

    def test_deserialize_dimensions_uses_single_thread_for_hierarchy_builds(self, tmp_path, monkeypatch):
        import threading
        import tm1_git_py.deserializer as deserializer_module

        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        thread_ids = set()
        original = deserializer_module._ensure_hierarchy_store_groups

        def _builder_spy(*args, **kwargs):
            thread_ids.add(threading.get_ident())
            return original(*args, **kwargs)

        monkeypatch.setattr(deserializer_module, "_ensure_hierarchy_store_groups", _builder_spy)

        deserialize_dimensions(dimensions_dir, tmp_path.name)
        assert len(thread_ids) == 1

    def test_deserialize_progress_sink_accepts_byte_events(self, tmp_path):
        import tm1_git_py.deserializer as deserializer_module

        model_dir = tmp_path / "model"
        dimensions_dir = model_dir / "dimensions"
        cubes_dir = model_dir / "cubes"
        dimensions_dir.mkdir(parents=True)
        cubes_dir.mkdir(parents=True)
        f1 = dimensions_dir / "a.json"
        f2 = cubes_dir / "b.json"
        f1.write_text("{}", encoding="utf-8")
        f2.write_text('{"k":1}', encoding="utf-8")

        progress = TqdmProgressSink(worker_count=1)
        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.START,
                scope=ProgressScope.WORKER,
                unit=ProgressUnit.BYTE,
                current=0,
                total=max(1, int(f1.stat().st_size)),
                message="reading file",
                path=str(f1),
            )
        )
        progress.on_event(
            ProgressEvent.make(
                kind=ProgressKind.UPDATE,
                scope=ProgressScope.WORKER,
                unit=ProgressUnit.BYTE,
                current=max(1, int(f1.stat().st_size)),
                total=max(1, int(f1.stat().st_size)),
                message="completed",
                path=str(f1),
            )
        )
        progress.close()

    def test_deserialize_subsets_rebuilds_store_only_when_subset_source_changes(self, tmp_path):
        src_dimensions = test_model_dir_base / "dimensions"
        dimensions_dir = tmp_path / "dimensions"
        shutil.copytree(src_dimensions, dimensions_dir)

        dimensions_before, _ = deserialize_dimensions(dimensions_dir, tmp_path.name)
        first_hier = dimensions_before["testbenchVersion"].hierarchies[0]
        before_subset_mtime = first_hier.subsets.source_json_mtime_ns()

        dimensions_again, _ = deserialize_dimensions(dimensions_dir, tmp_path.name)
        same_hier = dimensions_again["testbenchVersion"].hierarchies[0]
        assert same_hier.subsets.source_json_mtime_ns() == before_subset_mtime

    def test_store_backed_sequence_append_updates_signature(self, tmp_path):
        store = ModelStore.for_model_id(tmp_path.name)
        seq = StoreBackedSequence.for_elements_sink(
            store=store,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq.replace_with_payloads(())
        seq.append(Element(name="E1", type="Numeric"))
        seq.append(Element(name="E2", type="String"))
        signature = seq.sidecar_content_signature()
        assert signature is not None
        assert signature[0] == 2

    def test_store_backed_sequence_replace_and_filter_refresh_hash(self, tmp_path):
        store = ModelStore.for_model_id(tmp_path.name)
        seq = StoreBackedSequence.for_elements_sink(
            store=store,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq.replace_with_payloads(
            [
                {"Name": "A", "Type": "Numeric"},
                {"Name": "B", "Type": "Numeric"},
                {"Name": "C", "Type": "String"},
            ]
        )
        seq.replace_with_payloads(
            [
                {"Name": "A", "Type": "Numeric"},
                {"Name": "B", "Type": "String"},
            ]
        )
        seq.filter_in_place(lambda item: item.name != "A")
        signature = seq.sidecar_content_signature()
        assert signature is not None
        assert signature[0] == 1

    def test_store_backed_sequence_ordered_identity_iteration(self, tmp_path):
        store = ModelStore.for_model_id(tmp_path.name)
        seq = StoreBackedSequence.for_elements_sink(
            store=store,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq.replace_with_payloads(
            [
                {"Name": "C", "Type": "Numeric"},
                {"Name": "A", "Type": "Numeric"},
                {"Name": "B", "Type": "String"},
            ]
        )
        sorted_names = [
            Element.from_dict(payload).name
            for payload in seq.iter_payloads(ordered_by_identity=True)
        ]
        assert sorted_names == ["A", "B", "C"]

    def test_parallel_signature_elements_order_invariant(self, tmp_path):
        store_a = ModelStore.for_model_id("a")
        seq_a = StoreBackedSequence.for_elements_sink(
            store=store_a,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq_a.replace_with_payloads(
            [
                {"Name": "C", "Type": "Numeric"},
                {"Name": "A", "Type": "Numeric"},
                {"Name": "B", "Type": "String"},
            ]
        )
        sig_a = deserializer_module.recalculate_group_content_signature_parallel(
            store=store_a,
            group_id=seq_a.group_id,
            ordered_by_identity=True,
            chunk_size=1,
            progress_event_callback=CallbackProgressSink(lambda _event: None).on_event,
        )

        store_b = ModelStore.for_model_id("b")
        seq_b = StoreBackedSequence.for_elements_sink(
            store=store_b,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq_b.replace_with_payloads(
            [
                {"Name": "A", "Type": "Numeric"},
                {"Name": "B", "Type": "String"},
                {"Name": "C", "Type": "Numeric"},
            ]
        )
        sig_b = deserializer_module.recalculate_group_content_signature_parallel(
            store=store_b,
            group_id=seq_b.group_id,
            ordered_by_identity=True,
            chunk_size=2,
            progress_event_callback=CallbackProgressSink(lambda _event: None).on_event,
        )
        assert sig_a == sig_b

    def test_parallel_signature_edges_order_invariant(self, tmp_path):
        store_a = ModelStore.for_model_id("a")
        seq_a = StoreBackedSequence.for_edges_sink(
            store=store_a,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq_a.replace_with_payloads(
            [
                {"ParentName": "P2", "ComponentName": "C2", "Weight": 1},
                {"ParentName": "P1", "ComponentName": "C1", "Weight": 2},
                {"ParentName": "P1", "ComponentName": "C3", "Weight": 3},
            ]
        )
        sig_a = deserializer_module.recalculate_group_content_signature_parallel(
            store=store_a,
            group_id=seq_a.group_id,
            ordered_by_identity=True,
            chunk_size=1,
            progress_event_callback=CallbackProgressSink(lambda _event: None).on_event,
        )

        store_b = ModelStore.for_model_id("b")
        seq_b = StoreBackedSequence.for_edges_sink(
            store=store_b,
            dimension_name="MyDim",
            hierarchy_name="MyHier",
        )
        seq_b.replace_with_payloads(
            [
                {"ParentName": "P1", "ComponentName": "C1", "Weight": 2},
                {"ParentName": "P1", "ComponentName": "C3", "Weight": 3},
                {"ParentName": "P2", "ComponentName": "C2", "Weight": 1},
            ]
        )
        sig_b = deserializer_module.recalculate_group_content_signature_parallel(
            store=store_b,
            group_id=seq_b.group_id,
            ordered_by_identity=True,
            chunk_size=2,
            progress_event_callback=CallbackProgressSink(lambda _event: None).on_event,
        )
        assert sig_a == sig_b


    def test_deserialize_cubes(self, cubes_dir=test_model_dir_base / 'cubes'):
        expected_cube_names = ['testbenchSales']
        dimensions, errors = deserialize_dimensions(test_model_dir_base / "dimensions", test_model_dir_base.name)
        cubes, errors = deserialize_cubes(cubes_dir=cubes_dir, _dimensions=dimensions)
        diff_cube_names = set(expected_cube_names) - set(cubes.keys())
        assert len(diff_cube_names) == 0


    def test_deserialize_process(self, processes_dir=test_model_dir_base / 'processes'):
        processes, errors = deserialize_processes(process_dir=processes_dir)
        for process in processes.values():
            assert isinstance(process, Process)


    @pytest.mark.parametrize("data", dim_data)
    def test_deserialize_dimensions_error_propagation(self, tmp_path, data):
        dimensions_dir = tmp_path / "dimensions"
        dimensions_dir.mkdir()
        broken_dims = dimensions_dir / f"BrokenDimension.json"

        broken_dims.write_text(data, encoding="utf-8")

        dimensions, errors = deserialize_dimensions(dimensions_dir, tmp_path.name)

        assert not dimensions, f"Broken {type(dimensions.values())} file should not deserialize successfully"
        expected_key = Dimension.uri_for("BrokenDimension")
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )


    @pytest.mark.parametrize("data", chore_data)
    def test_deserialize_chore_error_propagation(self, tmp_path, data):
        chores_dir = tmp_path / "chores"
        chores_dir.mkdir()
        broken_chore = chores_dir / f"BrokenChores.json"

        broken_chore.write_text(data, encoding="utf-8")

        chores, errors = deserialize_chores(chores_dir)

        assert not chores, f"Broken {type(chores.values())} file should not deserialize successfully"
        expected_key = Chore.uri_for("BrokenChores")
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )


    @pytest.mark.parametrize("data", process_data)
    def test_deserialize_process_error_propagation(self, tmp_path, data):
        processes_dir = tmp_path / "processes"
        processes_dir.mkdir()
        broken_process = processes_dir / f"BrokenProcess.json"

        broken_process.write_text(data, encoding="utf-8")

        processes, errors = deserialize_processes(processes_dir)

        assert not processes, f"Broken {type(processes.values())} file should not deserialize successfully"
        expected_key = Process.uri_for("BrokenProcess")
        assert expected_key in errors, (
            f"Error key '{expected_key}' missing; collected keys: {list(errors.keys())}"
        )



class TestSerializer:

    def test_serializer_round_trip_sanity_check(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))
        model_deserialized, errors = deserialize_model(str(tmp_path))
        assert model.to_dict() == model_deserialized.to_dict()

        
    def test_serialize_dimensions_creates_hierarchy_and_subset_files(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))

        dim_dir = tmp_path / 'dimensions'
        assert dim_dir.exists()

        dimension = model.dimensions[0]
        dim_file = dim_dir / f"{dimension.name}.json"
        hierarchy_dir = dim_dir / f"{dimension.name}.hierarchies"
        hierarchy = dimension.hierarchies[0]
        hierarchy_file = hierarchy_dir / f"{hierarchy.name}.json"
        subset_dir = hierarchy_dir / f"{hierarchy.name}.subsets"

        assert dim_file.exists(), f"Dimension file missing: {dim_file}"
        dim_json = json.loads(dim_file.read_text(encoding='utf-8'))
        assert dim_json["Name"] == dimension.name

        assert hierarchy_file.exists(), f"Hierarchy file missing: {hierarchy_file}"
        hierarchy_json = json.loads(hierarchy_file.read_text(encoding='utf-8'))
        assert hierarchy_json["Name"] == hierarchy.name
        assert hierarchy_json["Elements"], "Hierarchy elements should be serialized"

        if hierarchy.subsets:
            for subset in hierarchy.subsets:
                subset_file = subset_dir / f"{subset.name}.json"
                assert subset_file.exists(), f"Subset file missing: {subset_file}"
                subset_json = json.loads(subset_file.read_text(encoding='utf-8'))
                assert subset_json["Name"] == subset.name


    def test_serialize_processes_creates_ti_and_json(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path))

        process_dir = tmp_path / 'processes'
        assert process_dir.exists()

        process = model.processes[0]
        json_file = process_dir / f"{process.name}.json"
        ti_file = process_dir / f"{process.name}.ti"

        assert json_file.exists(), f"Process JSON file missing: {json_file}"
        json_data = json.loads(json_file.read_text(encoding='utf-8'))
        assert json_data["Name"] == process.name
        assert json_data["Code@Code.link"] == process.code_link

        assert ti_file.exists(), f"Process TI file missing: {ti_file}"
        assert ti_file.read_text(encoding='utf-8') == process.ti.ti_as_string()


    def test_serialize_chores_creates_json(self, tmp_path):
        model = build_mock_model(include_chore=True)
        serialize_model(model, str(tmp_path))

        chore_dir = tmp_path / 'chores'
        assert chore_dir.exists()

        chore = model.chores[0]
        chore_file = chore_dir / f"{chore.name}.json"

        assert chore_file.exists(), f"Chore JSON file missing: {chore_file}"
        chore_data = json.loads(chore_file.read_text(encoding='utf-8'))
        assert chore_data["Name"] == chore.name
        assert chore_data["Tasks"] == chore.tasks


    def test_serialize_cubes_creates_json_views_and_rules(self, tmp_path):
        model = build_mock_model(include_rules=True, additional_views=True)
        serialize_model(model, str(tmp_path))

        cube_dir = tmp_path / 'cubes'
        assert cube_dir.exists()

        cube = model.cubes[0]
        cube_json = cube_dir / f"{cube.name}.json"
        rules_file = cube_dir / f"{cube.name}.rules"
        views_dir = cube_dir / f"{cube.name}.views"

        assert cube_json.exists(), f"Cube JSON missing: {cube_json}"
        assert json.loads(cube_json.read_text(encoding='utf-8'))["Name"] == cube.name

        if cube.rules:
            assert rules_file.exists(), "Rules file should exist when cube has rules"

        assert views_dir.exists(), "Views directory missing"
        for view in cube.views:
            view_json = views_dir / f"{view.name}.json"
            view_mdx = views_dir / f"{view.name}.mdx"
            assert view_json.exists() and view_mdx.exists(), (
                f"View files missing for {view.name}: {view_json}, {view_mdx}"
            )
            assert json.loads(view_json.read_text(encoding='utf-8'))["Name"] == view.name
            assert view_mdx.read_text(encoding='utf-8') == view.mdx


    def test_serialize_handles_special_character_names(self, tmp_path):
        special_dim_name = "}Tech Dimension"
        special_hier_name = "}Tech Hierarchy"
        special_cube_name = "}Tech Cube"
        special_view_name = "View With Space"
        special_process_name = "}Tech Process"

        hierarchy = Hierarchy(
            name=special_hier_name,
            elements=[Element(name="Item 1", type="Numeric")],
            edges=[],
            subsets=[],
        )
        dimension = Dimension(
            name=special_dim_name,
            hierarchies=[hierarchy],
            defaultHierarchy=hierarchy,
        )
        view = MDXView(
            name=special_view_name,
            mdx="SELECT {TM1SUBSETALL([}Tech Dimension].[}Tech Hierarchy])} ON 0 FROM [}Tech Cube]",
        )
        cube = Cube(
            name=special_cube_name,
            dimensions=[dimension],
            rules=[],
            views=[view],
        )
        ti_stub = TI("# prolog", "# metadata", "# data", "# epilog")
        process = Process(
            name=special_process_name,
            hasSecurityAccess=False,
            code_link=f"{special_process_name}.ti",
            datasource=None,
            parameters=[],
            variables=[],
            ti=ti_stub,
        )

        special_model = Model(
            cubes=[cube],
            dimensions=[dimension],
            processes=[process],
            chores=[]
        )

        serialize_model(special_model, str(tmp_path))

        dim_path = tmp_path / "dimensions" / f"{special_dim_name}.json"
        cube_path = tmp_path / "cubes" / f"{special_cube_name}.json"
        view_json_path = tmp_path / "cubes" / f"{special_cube_name}.views" / f"{special_view_name}.json"
        process_json_path = tmp_path / "processes" / f"{special_process_name}.json"

        for path in [dim_path, cube_path, view_json_path, process_json_path]:
            assert path.exists(), f"Serialized file missing: {path}"



class TestComparator:

    mock_changeset_data = _build_mock_changeset_data()

    @staticmethod
    def _changes_by_type(changeset: Changeset, change_type: ChangeType) -> list[Change]:
        return [c for c in changeset.changes if c.change_type == change_type]

    @staticmethod
    def _bodies_by(change_set: list[Change], body_type: type) -> list:
        return [c.body for c in change_set if isinstance(c.body, body_type)]


    def test_objects_equal(self, objects_equal_data):
        obj1, obj2, shallow_fn, expect_strict_equal = objects_equal_data

        if expect_strict_equal:
            assert obj1 == obj2
        else:
            assert obj1 != obj2

        if shallow_fn:
            assert shallow_fn(obj1, obj2)

    def test_comparator_detects_edge_weight_only_change_in_memory(self):
        """Edge-only weight delta must appear as an Edge MODIFY (list-backed hierarchies)."""
        hier_old = Hierarchy(
            name="H",
            elements=[
                Element(name="A", type="Numeric"),
                Element(name="B", type="Numeric"),
            ],
            edges=[Edge("A", "B", 1)],
            subsets=[],
        )
        hier_new = Hierarchy(
            name="H",
            elements=[
                Element(name="A", type="Numeric"),
                Element(name="B", type="Numeric"),
            ],
            edges=[Edge("A", "B", 2)],
            subsets=[],
        )
        dim_old = Dimension(name="D", hierarchies=[hier_old], defaultHierarchy=hier_old)
        dim_new = Dimension(name="D", hierarchies=[hier_new], defaultHierarchy=hier_new)
        model_old = Model(dimensions=[dim_old], cubes=[], processes=[], chores=[])
        model_new = Model(dimensions=[dim_new], cubes=[], processes=[], chores=[])
        changeset = Comparator().compare(model_old, model_new, mode="full")
        modified_edges = self._bodies_by(
            self._changes_by_type(changeset, ChangeType.MODIFY),
            Edge,
        )
        assert len(modified_edges) == 1 and modified_edges[0].weight == 2

    def test_edge_store_content_signature_includes_weight(self, tmp_path):
        """Regression: parallel edge hash must depend on Weight or comparator skips streaming diff."""
        store = ModelStore.for_model_id(f"edge_sig_{tmp_path.name}")
        seq = StoreBackedSequence.for_edges_sink(
            store=store,
            model_id=f"edge_sig_{tmp_path.name}",
            dimension_name="D",
            hierarchy_name="H",
        )
        seq.replace_with_payloads(())
        seq.append(Edge("P", "C", 1))
        seq.recalculate_content_signature_parallel()
        sig_w1 = seq.sidecar_content_signature()
        seq.replace_with_payloads(())
        seq.append(Edge("P", "C", 2))
        seq.recalculate_content_signature_parallel()
        sig_w2 = seq.sidecar_content_signature()
        assert sig_w1 != sig_w2


    def test_comparator_no_changes_round_trip(self, tmp_path):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        serialize_model(model=model1, dir=str(tmp_path))
        model2, error2 = deserialize_model(str(tmp_path))
        
        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        assert len(changeset.changes) == 0


    def test_comparator_has_changes_add_only(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='add_only')
        added = self._changes_by_type(changeset, ChangeType.ADD)
        modified = self._changes_by_type(changeset, ChangeType.MODIFY)
        removed = self._changes_by_type(changeset, ChangeType.REMOVE)

        assert len(added) == 6
        assert len(modified) == 5
        assert len(removed) == 0


    def test_comparator_has_changes_full(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._changes_by_type(changeset, ChangeType.ADD)
        modified = self._changes_by_type(changeset, ChangeType.MODIFY)
        removed = self._changes_by_type(changeset, ChangeType.REMOVE)

        assert len(added) == 6
        assert len(modified) == 5
        assert len(removed) == 6


    def test_comparator_dimensions_change_propagation(self):
        """Test if adding a new Subset does not propagate as a change to the Dimension object"""
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._bodies_by(self._changes_by_type(changeset, ChangeType.ADD), Subset)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Hierarchy)

        assert (isinstance(added[0], Subset) and added[0].name == "}Temp_Subset_Discount")
        assert not modified

    def test_comparator_adds_dimension_children_when_dimension_missing(self):
        hierarchy_obj = Hierarchy(
            name="DimA",
            elements=[Element(name="Elem1", type="Numeric")],
            edges=[],
            subsets=[],
        )
        dimension_obj = Dimension(
            name="DimA",
            hierarchies=[hierarchy_obj],
            defaultHierarchy=hierarchy_obj,
        )
        model_old = Model(cubes=[], dimensions=[], processes=[], chores=[])
        model_new = Model(cubes=[], dimensions=[dimension_obj], processes=[], chores=[])

        changeset = Comparator().compare(model_old, model_new, mode="full")
        added = self._changes_by_type(changeset, ChangeType.ADD)

        assert any(isinstance(change.body, Dimension) and change.body.name == "DimA" for change in added)
        assert any(
            isinstance(change.body, Hierarchy) and change.uri == "Dimensions('DimA')/Hierarchies('DimA')"
            for change in added
        )
        assert any(
            isinstance(change.body, Element)
            and change.uri == "Dimensions('DimA')/Hierarchies('DimA')/Elements('Elem1')"
            for change in added
        )


    def test_comparator_cubes_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        added = self._bodies_by(self._changes_by_type(changeset, ChangeType.ADD), MDXView)
        removed = self._bodies_by(self._changes_by_type(changeset, ChangeType.REMOVE), MDXView)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Cube)
        modified_rule_changes = [
            c for c in self._changes_by_type(changeset, ChangeType.MODIFY)
            if c.object_type == ObjectType.RULE
        ]
        modified_rules = [c.body for c in modified_rule_changes]

        old_cube = next(c for c in model1.cubes if c.name == "testbenchSales")
        new_cube = next(c for c in model2.cubes if c.name == "testbenchSales")

        assert (isinstance(added[0], MDXView) and added[0].name == "tm1_bedrock_py_gp0vkg064lilmmga")
        assert not modified
        assert (old_cube.rules != new_cube.rules)
        assert len(modified_rules) == 1
        unified_rule = modified_rules[0]
        assert isinstance(unified_rule, Rule)
        assert unified_rule.name == "default"
        assert modified_rule_changes[0].uri == "Cubes('testbenchSales')/Rules('default')"
        assert unified_rule.full_statement == new_cube.get_rule_text()
        assert (isinstance(removed[0], MDXView) and removed[0].name == "tm1_bedrock_py_fp0vkg064lilmmga")

    def test_comparator_skips_store_backed_compare_when_hash_matches(self, tmp_path, caplog):
        store = ModelStore.for_model_id(tmp_path.name)

        def disk_elements(group_suffix: str, *elems: Element) -> StoreBackedSequence:
            db = StoreBackedSequence.for_elements_sink(
                store=store,
                dimension_name="DimA",
                hierarchy_name=group_suffix,
            )
            db.replace_with_payloads([e.to_dict() for e in sorted(elems, key=lambda x: (x.name or ""))])
            return db

        h_old = Hierarchy(
            name="H1",
            elements=disk_elements(
                "H1_old_hash",
                Element(name="A", type="Numeric"),
                Element(name="B", type="Numeric"),
            ),
            edges=[],
            subsets=[],
        )
        h_new = Hierarchy(
            name="H1",
            elements=disk_elements(
                "H1_new_hash",
                Element(name="A", type="Numeric"),
                Element(name="B", type="Numeric"),
            ),
            edges=[],
            subsets=[],
        )
        d_old = Dimension(name="DimA", hierarchies=[h_old], defaultHierarchy=h_old)
        d_new = Dimension(name="DimA", hierarchies=[h_new], defaultHierarchy=h_new)
        model_old = Model(dimensions=[d_old], cubes=[], processes=[], chores=[])
        model_new = Model(dimensions=[d_new], cubes=[], processes=[], chores=[])

        caplog.set_level("INFO")
        changeset = Comparator().compare(model_old, model_new, mode="full")

        assert len(changeset.changes) == 0
        assert "Skipping Element streaming compare: count+hash match" in caplog.text

    def test_comparator_ignores_leaf_hierarchy_elements_by_default(self):
        model1 = build_mock_model()
        model2 = build_mock_model()

        leaf_hierarchy_old = Hierarchy(
            name="Leaves",
            elements=[Element(name="LeafA", type="Numeric")],
            edges=[],
            subsets=[],
        )
        leaf_hierarchy_new = Hierarchy(
            name="Leaves",
            elements=[
                Element(name="LeafA", type="Numeric"),
                Element(name="LeafB", type="Numeric"),
            ],
            edges=[],
            subsets=[],
        )
        model1.dimensions[0].hierarchies.append(leaf_hierarchy_old)
        model2.dimensions[0].hierarchies.append(leaf_hierarchy_new)

        changeset = Comparator().compare(model1, model2, mode='full')
        leaf_element_changes = [
            change for change in changeset.changes
            if change.object_type == ObjectType.ELEMENT and "Hierarchies('Leaves')" in change.uri
        ]
        assert not leaf_element_changes

    def test_comparator_can_force_include_leaves_hierarchy_via_filter_rules(self):
        model1 = build_mock_model()
        model2 = build_mock_model()

        leaf_hierarchy_old = Hierarchy(
            name="Leaves",
            elements=[Element(name="LeafA", type="Numeric")],
            edges=[],
            subsets=[],
        )
        leaf_hierarchy_new = Hierarchy(
            name="Leaves",
            elements=[
                Element(name="LeafA", type="Numeric"),
                Element(name="LeafB", type="Numeric"),
            ],
            edges=[],
            subsets=[],
        )
        model1.dimensions[0].hierarchies.append(leaf_hierarchy_old)
        model2.dimensions[0].hierarchies.append(leaf_hierarchy_new)

        changeset = Comparator().compare(
            model1,
            model2,
            mode="full",
            filter_rules=["!Dimensions('*')/Hierarchies('Leaves')"],
        )
        leaf_element_changes = [
            change
            for change in changeset.changes
            if change.object_type == ObjectType.ELEMENT
            and "Hierarchies('Leaves')" in change.uri
        ]
        assert len(leaf_element_changes) == 1
        assert leaf_element_changes[0].change_type == ChangeType.ADD
        assert leaf_element_changes[0].body.name == "LeafB"

    def test_comparator_streaming_store_backed_elements_and_edges(self, tmp_path):
        """StoreBackedSequence merge compare should match in-memory list compare."""

        store = ModelStore.for_model_id(tmp_path.name)

        def disk_elements(group_suffix: str, *elems: Element) -> StoreBackedSequence:
            db = StoreBackedSequence.for_elements_sink(
                store=store,
                dimension_name="DimA",
                hierarchy_name=group_suffix,
            )
            db.replace_with_payloads(())
            for e in sorted(elems, key=lambda x: (x.name or "")):
                db.append(e)
            return db

        def disk_edges(group_suffix: str, *edges: Edge) -> StoreBackedSequence:
            db = StoreBackedSequence.for_edges_sink(
                store=store,
                dimension_name="DimA",
                hierarchy_name=group_suffix,
            )
            db.replace_with_payloads(())
            for ed in sorted(edges, key=lambda x: (x.parent or "", x.component_name or "")):
                db.append(ed)
            return db

        h_old = Hierarchy(
            name="H1",
            elements=disk_elements(
                "H1_old",
                Element(name="A", type="Numeric"),
                Element(name="C", type="Numeric"),
            ),
            edges=disk_edges(
                "H1_old",
                Edge(parent="R", component_name="A", weight=1.0),
            ),
            subsets=[],
        )
        h_new = Hierarchy(
            name="H1",
            elements=disk_elements(
                "H1_new",
                Element(name="A", type="String"),
                Element(name="B", type="Numeric"),
                Element(name="C", type="Numeric"),
            ),
            edges=disk_edges(
                "H1_new",
                Edge(parent="R", component_name="A", weight=2.0),
                Edge(parent="R", component_name="B", weight=1.0),
            ),
            subsets=[],
        )
        d_disk_old = Dimension(name="DimA", hierarchies=[h_old], defaultHierarchy=h_old)
        d_disk_new = Dimension(name="DimA", hierarchies=[h_new], defaultHierarchy=h_new)

        mh_old = Hierarchy(
            name="H1",
            elements=[
                Element(name="A", type="Numeric"),
                Element(name="C", type="Numeric"),
            ],
            edges=[Edge(parent="R", component_name="A", weight=1.0)],
            subsets=[],
        )
        mh_new = Hierarchy(
            name="H1",
            elements=[
                Element(name="A", type="String"),
                Element(name="B", type="Numeric"),
                Element(name="C", type="Numeric"),
            ],
            edges=[
                Edge(parent="R", component_name="A", weight=2.0),
                Edge(parent="R", component_name="B", weight=1.0),
            ],
            subsets=[],
        )
        d_mem_old = Dimension(name="DimA", hierarchies=[mh_old], defaultHierarchy=mh_old)
        d_mem_new = Dimension(name="DimA", hierarchies=[mh_new], defaultHierarchy=mh_new)

        model_disk = Model(dimensions=[d_disk_old], cubes=[], processes=[], chores=[])
        model_disk_b = Model(dimensions=[d_disk_new], cubes=[], processes=[], chores=[])
        model_mem = Model(dimensions=[d_mem_old], cubes=[], processes=[], chores=[])
        model_mem_b = Model(dimensions=[d_mem_new], cubes=[], processes=[], chores=[])

        comp = Comparator()
        cs_disk = comp.compare(model_disk, model_disk_b, mode="full")
        cs_mem = comp.compare(model_mem, model_mem_b, mode="full")

        def change_key(c: Change) -> tuple:
            return (
                c.change_type,
                c.object_type,
                c.uri,
                type(c.body).__name__,
                getattr(c.body, "name", None),
                getattr(c.body, "parent", None),
                getattr(c.body, "component_name", None),
                getattr(c.body, "type", None),
                getattr(c.body, "weight", None),
            )

        assert sorted(change_key(c) for c in cs_disk.changes) == sorted(
            change_key(c) for c in cs_mem.changes
        )

    def test_comparator_sorts_unsorted_store_backed_lists_before_merge(self, tmp_path):
        store = ModelStore.for_model_id(tmp_path.name)

        def disk_elements_unsorted(group_suffix: str, *elems: Element) -> StoreBackedSequence:
            db = StoreBackedSequence.for_elements_sink(
                store=store,
                dimension_name="DimA",
                hierarchy_name=group_suffix,
            )
            db.replace_with_payloads(())
            for e in elems:
                db.append(e)
            return db

        old_elements = disk_elements_unsorted(
            "H1_old_unsorted",
            Element(name="C", type="Numeric"),
            Element(name="A", type="Numeric"),
        )
        new_elements = disk_elements_unsorted(
            "H1_new_unsorted",
            Element(name="A", type="Numeric"),
            Element(name="B", type="Numeric"),
            Element(name="C", type="Numeric"),
        )

        h_old = Hierarchy(name="H1", elements=old_elements, edges=[], subsets=[])
        h_new = Hierarchy(name="H1", elements=new_elements, edges=[], subsets=[])
        d_old = Dimension(name="DimA", hierarchies=[h_old], defaultHierarchy=h_old)
        d_new = Dimension(name="DimA", hierarchies=[h_new], defaultHierarchy=h_new)

        changeset = Comparator().compare(
            Model(dimensions=[d_old], cubes=[], processes=[], chores=[]),
            Model(dimensions=[d_new], cubes=[], processes=[], chores=[]),
            mode="full",
        )

        add_names = sorted(
            c.body.name
            for c in changeset.changes
            if c.change_type == ChangeType.ADD and c.object_type == ObjectType.ELEMENT
        )
        remove_names = sorted(
            c.body.name
            for c in changeset.changes
            if c.change_type == ChangeType.REMOVE and c.object_type == ObjectType.ELEMENT
        )
        assert add_names == ["B"]
        assert remove_names == []

    def test_comparator_uses_identity_ordered_iteration_for_store_backed_lists(self, tmp_path):
        store = ModelStore.for_model_id(tmp_path.name)

        def disk_elements_sorted(group_suffix: str, *elems: Element) -> StoreBackedSequence:
            db = StoreBackedSequence.for_elements_sink(
                store=store,
                dimension_name="DimA",
                hierarchy_name=group_suffix,
            )
            db.replace_with_payloads(())
            for e in sorted(elems, key=lambda x: (x.name or "", x.type or "")):
                db.append(e)
            return db

        old_elements = disk_elements_sorted(
            "H1_old_sorted",
            Element(name="A", type="Numeric"),
            Element(name="C", type="Numeric"),
        )
        new_elements = disk_elements_sorted(
            "H1_new_sorted",
            Element(name="A", type="Numeric"),
            Element(name="B", type="Numeric"),
            Element(name="C", type="Numeric"),
        )

        h_old = Hierarchy(name="H1", elements=old_elements, edges=[], subsets=[])
        h_new = Hierarchy(name="H1", elements=new_elements, edges=[], subsets=[])
        d_old = Dimension(name="DimA", hierarchies=[h_old], defaultHierarchy=h_old)
        d_new = Dimension(name="DimA", hierarchies=[h_new], defaultHierarchy=h_new)
        changeset = Comparator().compare(
            Model(dimensions=[d_old], cubes=[], processes=[], chores=[]),
            Model(dimensions=[d_new], cubes=[], processes=[], chores=[]),
            mode="full",
        )
        add_names = sorted(
            c.body.name
            for c in changeset.changes
            if c.change_type == ChangeType.ADD and c.object_type == ObjectType.ELEMENT
        )
        assert add_names == ["B"]

    def test_comparator_tracks_native_view_changes(self):
        model1 = build_mock_model()
        model2 = build_mock_model()

        model2.cubes[0].views.append(
            NativeView(
                name="DefaultNative",
                columns=[],
                rows=[],
                titles=[],
                suppress_empty_columns=True,
                suppress_empty_rows=True,
                format_string="0.#########",
            )
        )

        changeset = Comparator().compare(model1, model2, mode='full')
        native_adds = [
            change for change in changeset.changes
            if change.change_type == ChangeType.ADD and change.object_type == ObjectType.NATIVE_VIEW
        ]
        assert len(native_adds) == 1
        assert native_adds[0].body.name == "DefaultNative"


    def test_comparator_process_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        removed = self._bodies_by(self._changes_by_type(changeset, ChangeType.REMOVE), Process)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Process)

        assert (isinstance(removed[0], Process) and removed[0].name == "Mock Process Load Product Data")
        assert (isinstance(modified[0], Process) and modified[0].name == "Mock Process Export Dimension")


    def test_comparator_chores_change_propagation(self):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        model2, error2 = deserialize_model(str(test_model_dir_diff))

        expected_chores = ["Mock Nightly Maintenance", "Mock Weekly Export"]

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        removed = self._bodies_by(self._changes_by_type(changeset, ChangeType.REMOVE), Chore)
        modified = self._bodies_by(self._changes_by_type(changeset, ChangeType.MODIFY), Chore)

        for chore_new in modified:
            assert (isinstance(chore_new, Chore) and chore_new.name in expected_chores )
        assert any(isinstance(chore_old, Chore) and chore_old.name == "Mock Weekly Export" for chore_old in removed)

    def test_comparator_emits_progress_events_with_sink(self):
        class _CaptureSink:
            def __init__(self):
                self.events = []

            def on_event(self, event):
                self.events.append(event)

            def close(self):
                return

        model1 = build_mock_model()
        model2 = build_mock_model()
        sink = _CaptureSink()

        changeset = Comparator().compare(
            model1,
            model2,
            mode="full",
            progress_sink=sink,
        )

        assert isinstance(changeset, Changeset)
        assert any(event.scope.value == "TOTAL" and event.kind.value == "start" for event in sink.events)
        assert any(event.scope.value == "TOTAL" and event.kind.value == "update" for event in sink.events)

    def test_comparator_result_unchanged_with_progress_sink(self):
        class _NoopSink:
            def on_event(self, event):
                _ = event

            def close(self):
                return

        model_old = build_mock_model()
        model_new = build_mock_model()
        model_new.cubes[0].views.append(
            NativeView(
                name="ExtraNative",
                columns=[],
                rows=[],
                titles=[],
                suppress_empty_columns=True,
                suppress_empty_rows=True,
                format_string="0.#########",
            )
        )

        cs_plain = Comparator().compare(model_old, model_new, mode="full")
        cs_with_progress = Comparator().compare(
            model_old,
            model_new,
            mode="full",
            progress_sink=_NoopSink(),
        )

        def _change_key(change: Change) -> tuple:
            return (
                change.change_type,
                change.object_type,
                change.uri,
                type(change.body).__name__,
                getattr(change.body, "name", None),
                getattr(change.body, "parent", None),
                getattr(change.body, "component_name", None),
                getattr(change.body, "type", None),
                getattr(change.body, "weight", None),
            )

        assert sorted(_change_key(item) for item in cs_plain.changes) == sorted(
            _change_key(item) for item in cs_with_progress.changes
        )



class TestExporter:

    def test_export_forwards_max_workers_to_dimensions(self):
        tm1_conn = mock.Mock()
        with mock.patch.object(exporter_module, "dimensions_to_model", return_value=({}, {})) as mock_dims, \
             mock.patch.object(exporter_module, "cubes_to_model", return_value=({}, {})), \
             mock.patch.object(exporter_module, "procs_to_model", return_value=({}, {})), \
             mock.patch.object(exporter_module, "chores_to_model", return_value=({}, {})):
            exporter_module.export(tm1_conn, model_id="unit-export", max_workers=9)
        assert mock_dims.call_args.kwargs.get("max_workers") == 9

    def test_compare_worker_split_helper(self):
        assert main_module._split_compare_workers(8) == (4, 4)
        assert main_module._split_compare_workers(7) == (3, 4)
        assert main_module._split_compare_workers(1) == (1, 1)

    def test_process_service_get_all_names_page_builds_query(self, mocker):
        tm1_conn = mocker.Mock()
        response = mocker.Mock()
        response.json.return_value = {"value": [{"Name": "P1"}], "@odata.count": 10}
        tm1_conn.connection.GET.return_value = response

        result = process_service_ext._get_all_names_page(
            tm1_conn,
            filter="contains(Name,'Load')",
            skip=5,
            top=25,
            count=True,
        )

        tm1_conn.connection.GET.assert_called_once_with(
            "/Processes?$select=Name&$filter=contains(Name,'Load')&$skip=5&$top=25&$count=true"
        )
        assert result.names == ["P1"]
        assert result.count == 10
        assert result.skip == 5
        assert result.top == 25

    def test_process_service_get_all_names_paginates(self, mocker):
        tm1_conn = mocker.Mock()

        mocker.patch.object(
            process_service_ext,
            "_get_all_names_page",
            side_effect=[
                process_service_ext.ProcessNamesResult(names=["P1"], count=2, skip=0, top=1),
                process_service_ext.ProcessNamesResult(names=["P2"], count=None, skip=1, top=1),
            ],
        )

        result = process_service_ext.get_all_names(
            tm1_conn,
            filter="contains(Name,'Load')",
            page_size=1,
        )

        assert result == ["P1", "P2"]

    def test_procs_to_model_uses_process_service_ext_names(self, mocker):
        from tm1_git_py.exporter import procs_to_model

        tm1_conn = mocker.Mock()
        tm1_conn.processes.get_all_names = mocker.Mock()
        mock_get_process_names = mocker.patch(
            "tm1_git_py.exporter.get_process_names",
            return_value=["MyProcess"],
        )

        tm1_conn.processes.get.return_value = types.SimpleNamespace(
            name="MyProcess",
            has_security_access=True,
            parameters=[],
            variables=[],
            prolog_procedure="",
            metadata_procedure="",
            data_procedure="",
            epilog_procedure="",
        )

        processes, errors = procs_to_model(
            tm1_conn,
            filter_rules=FilterRules([]),
        )

        mock_get_process_names.assert_called_once_with(
            tm1_conn,
            filter=None,
        )
        tm1_conn.processes.get_all_names.assert_not_called()
        assert "MyProcess" in processes
        assert errors == {}

    def test_cube_service_get_all_names_page_builds_query(self, mocker):
        tm1_conn = mocker.Mock()
        response = mocker.Mock()
        response.json.return_value = {"value": [{"Name": "Sales"}], "@odata.count": 5}
        tm1_conn.connection.GET.return_value = response

        result = cube_service_ext._get_all_names_page(
            tm1_conn,
            filter="startswith(Name,'Sales')",
            skip=10,
            top=50,
            count=True,
        )

        tm1_conn.connection.GET.assert_called_once_with(
            "/Cubes?$select=Name&$filter=startswith(Name,'Sales')&$skip=10&$top=50&$count=true"
        )
        assert result.names == ["Sales"]
        assert result.count == 5
        assert result.skip == 10
        assert result.top == 50

    def test_cubes_to_model_uses_cube_service_ext_names(self, mocker):
        from tm1_git_py.exporter import cubes_to_model

        tm1_conn = mocker.Mock()
        tm1_conn.cubes.get_all_names = mocker.Mock()
        mock_get_cube_names = mocker.patch(
            "tm1_git_py.exporter.get_cube_names",
            return_value=[],
        )

        cubes, errors = cubes_to_model(
            tm1_conn,
            _dimensions={},
            filter_rules=FilterRules(["Cubes('Sales*')"]),
        )

        assert cubes == {}
        assert errors == {}
        _, kwargs = mock_get_cube_names.call_args
        assert kwargs["filter"] is not None
        tm1_conn.cubes.get_all_names.assert_not_called()

    def test_view_service_get_all_builds_filtered_urls(self, mocker):
        tm1_conn = mocker.Mock()
        mocker.patch.object(
            view_service_ext.MDXView,
            "from_dict",
            return_value=types.SimpleNamespace(name="V1"),
        )
        mocker.patch.object(
            view_service_ext.NativeView,
            "from_dict",
            return_value=types.SimpleNamespace(name="N1"),
        )
        tm1_conn.connection.GET.side_effect = [
            mocker.Mock(
                json=mocker.Mock(
                    return_value={
                        "value": [
                            {"@odata.type": "#ibm.tm1.api.v1.MDXView", "Name": "V1", "MDX": "SELECT 1 ON 0"}
                        ]
                    }
                )
            ),
            mocker.Mock(json=mocker.Mock(return_value={"value": []})),
        ]

        private_views, public_views = view_service_ext.get_all(
            tm1_conn,
            cube_name="Sales",
            filter="startswith(Name,'Main')",
        )

        assert len(private_views) == 1
        assert len(public_views) == 0
        first_url = tm1_conn.connection.GET.call_args_list[0].args[0]
        second_url = tm1_conn.connection.GET.call_args_list[1].args[0]
        assert "/Cubes('Sales')/PrivateViews?" in first_url
        assert "&$filter=startswith(Name,'Main')" in first_url
        assert "/Cubes('Sales')/Views?" in second_url
        assert "&$filter=startswith(Name,'Main')" in second_url

    def test_cubes_to_model_uses_view_service_ext(self, mocker):
        from tm1_git_py.exporter import cubes_to_model

        tm1_conn = mocker.Mock()
        mocker.patch("tm1_git_py.exporter.get_cube_names", return_value=["Sales"])
        mock_get_views = mocker.patch("tm1_git_py.exporter.get_views", return_value=([], []))
        tm1_conn.cubes.get.return_value = types.SimpleNamespace(
            dimensions=[],
            has_rules=False,
            rules=types.SimpleNamespace(body=""),
        )

        cubes_to_model(
            tm1_conn,
            _dimensions={},
            filter_rules=FilterRules(["!Cubes('Sales')/Views('Main*')"]),
        )

        _, kwargs = mock_get_views.call_args
        assert kwargs["cube_name"] == "Sales"
        assert kwargs["filter"] is not None

    def test_export_no_filter_rules_disables_skip_control_flags(self, mocker):
        tm1_service = mocker.Mock()
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mock_chores = mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        model, errors = export(tm1_service, model_id="unit-export", filter_rules_list=None)

        assert isinstance(model, Model)
        assert errors == {"dim": {}, "cube": {}, "process": {}, "chore": {}}
        mock_dimensions.assert_called_once()
        args, kwargs = mock_dimensions.call_args
        assert kwargs["filter_rules"]._normalized_rules == with_default_leaves_ignore([])

    def test_export_non_technical_filter_rules_keep_skip_control_disabled(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["Processes('MyProcess*')"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, model_id="unit-export", filter_rules_list=filter_rules)

        expected_pf = FilterRules(with_default_leaves_ignore(filter_rules))
        mock_dimensions.assert_called_once()
        args, kwargs = mock_dimensions.call_args
        assert kwargs["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_cubes.assert_called_once()
        _, cube_kw = mock_cubes.call_args
        assert cube_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_processes.assert_called_once()
        _, proc_kw = mock_processes.call_args
        assert proc_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules

    def test_export_technical_intent_filter_rules_enable_skip_control_flags(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["Dimensions('}*')", "Cubes('}*')", "Processes('}*')"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, model_id="unit-export", filter_rules_list=filter_rules)

        expected_pf = FilterRules(with_default_leaves_ignore(filter_rules))
        mock_dimensions.assert_called_once()
        args, kwargs = mock_dimensions.call_args
        assert kwargs["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_cubes.assert_called_once()
        _, cube_kw = mock_cubes.call_args
        assert cube_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_processes.assert_called_once()
        _, proc_kw = mock_processes.call_args
        assert proc_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules

    def test_export_custom_filter_rules_are_forwarded_as_is(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["Dimensions('TestDim1*')", "Cubes('TestCube1*')"]
        mock_dimensions = mocker.patch("tm1_git_py.exporter.dimensions_to_model", return_value=({}, {}))
        mock_cubes = mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mock_processes = mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, model_id="unit-export", filter_rules_list=filter_rules)

        expected_pf = FilterRules(with_default_leaves_ignore(filter_rules))
        mock_dimensions.assert_called_once()
        args, kwargs = mock_dimensions.call_args
        assert kwargs["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_cubes.assert_called_once()
        _, cube_kw = mock_cubes.call_args
        assert cube_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        _, proc_kw = mock_processes.call_args
        assert proc_kw["filter_rules"]._normalized_rules == expected_pf._normalized_rules
        mock_processes.assert_called_once_with(
            tm1_service,
            filter_rules=mocker.ANY,
        )

    def test_export_force_include_leaves_does_not_inject_default_leaves_exclude(self, mocker):
        tm1_service = mocker.Mock()
        filter_rules = ["!Dimensions('*')/Hierarchies('Leaves')"]
        mock_dimensions = mocker.patch(
            "tm1_git_py.exporter.dimensions_to_model",
            return_value=({}, {}),
        )
        mocker.patch("tm1_git_py.exporter.cubes_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.procs_to_model", return_value=({}, {}))
        mocker.patch("tm1_git_py.exporter.chores_to_model", return_value=({}, {}))

        export(tm1_service, model_id="unit-export", filter_rules_list=filter_rules)

        _, kwargs = mock_dimensions.call_args
        assert kwargs["filter_rules"]._normalized_rules == filter_rules

    def test_should_exclude_path_supports_tm1project_filter_format(self):
        filter_rules = [
            "Cubes('views*')",
            "Dimensions('product*')",
            "Processes('zsys analogic operation version copy*')",
        ]

        assert should_exclude_path("Cubes('viewsSales')", filter_rules)
        assert should_exclude_path("Dimensions('ProductHierarchy')", filter_rules)
        assert should_exclude_path(
            "Processes('zsys analogic operation version copy')",
            filter_rules,
        )
        assert not should_exclude_path("Cubes('SalesCube')", filter_rules)

    def test_import_filter_ignores_hash_comment_lines(self, tmp_path):
        rules_file = tmp_path / "filter.txt"
        rules_file.write_text(
            "# comment line\n"
            "Dimensions('A*')\n"
            "   # spaced comment line\n"
            "\n"
            "Cubes('Sales*')\n",
            encoding="utf-8",
        )

        rules = filter_module.import_filter(str(rules_file))
        assert rules == ["Dimensions('A*')", "Cubes('Sales*')"]

    def test_path_filter_should_exclude(self):
        pf = FilterRules(["Dimensions('product*')", "Cubes('views*')"])
        assert pf.should_exclude("Dimensions('ProductHierarchy')")
        assert pf.should_exclude("Cubes('viewsSales')")
        assert not pf.should_exclude("Cubes('SalesCube')")

    def test_path_filter_force_include_element_keeps_parent_path_only(self):
        pf = FilterRules(
            [
                "Dimensions('Sales')",
                "!Dimensions('Sales')/Hierarchies('Main')/Elements('LeafA')",
            ]
        )
        assert not pf.should_exclude("Dimensions('Sales')")
        assert not pf.should_exclude("Dimensions('Sales')/Hierarchies('Main')")
        assert not pf.should_exclude("Dimensions('Sales')/Hierarchies('Main')/Elements('LeafA')")
        assert pf.should_exclude("Dimensions('Sales')/Hierarchies('Main')/Elements('LeafB')")
        assert pf.should_exclude("Dimensions('Sales')/Hierarchies('Other')")

    def test_path_filter_force_include_hierarchy_keeps_only_related_hierarchy(self):
        pf = FilterRules(
            [
                "Dimensions('Sales')",
                "!Dimensions('Sales')/Hierarchies('Main')",
            ]
        )
        assert not pf.should_exclude("Dimensions('Sales')")
        assert not pf.should_exclude("Dimensions('Sales')/Hierarchies('Main')")
        assert not pf.should_exclude("Dimensions('Sales')/Hierarchies('Main')/Elements('LeafA')")
        assert pf.should_exclude("Dimensions('Sales')/Hierarchies('Other')")

    def test_path_filter_element_validation_accepts_startswith_endswith(self):
        """URL identifier patterns with * at start or end are valid."""
        pf = FilterRules(["Dimensions('prod*')"])
        assert pf.has_rules
        pf2 = FilterRules(["Dimensions('*prod')"])
        assert pf2.has_rules

    def test_path_filter_element_validation_rejects_wildcard_in_middle(self):
        """URL identifier patterns with * in middle are invalid and skipped."""
        pf = FilterRules(["Dimensions('asd*asd')"])
        assert not pf.has_rules

    def test_get_relevant_name_rules_for_dimension(self):
        pf = FilterRules(
            [
                "Dimensions('BW*')",
                "!Dimensions('BW Comp*')",
                "Cubes('Sales*')",
                "Dimensions('Product')/Hierarchies('Main')",
            ]
        )

        assert pf.get_rules_for_entity("dimension") == [
            "Dimensions('BW*')",
            "!Dimensions('BW Comp*')",
        ]

    def test_get_rules_for_entity_uses_entity_regex(self):
        pf = FilterRules(
            [
                "Dimensions('*')/Hierarchies('}*')",
                "!Dimensions('Sales')/Hierarchies('Main*')",
                "Dimensions('Sales')/Hierarchies('Main*')/Elements('X*')",
                "Chores('Daily*')/Tasks('LoadData')",
            ]
        )

        assert pf.get_rules_for_entity("hierarchy") == [
            "Dimensions('*')/Hierarchies('}*')",
            "!Dimensions('Sales')/Hierarchies('Main*')",
        ]
        assert pf.get_rules_for_entity("task") == [
            "Chores('Daily*')/Tasks('LoadData')",
        ]

    def test_to_tm1_name_filter_for_dimension_with_include_and_exclude(self):
        pf = FilterRules(
            [
                "Dimensions('BW Comp*')",
                "!Dimensions('BW*')",
            ]
        )

        result = pf.to_tm1_name_filter("dimension")
        assert result.filter_expr == "(not (startswith(Name, 'BW Comp'))) or (startswith(Name, 'BW'))"
        assert result.skip_all is False

    def test_to_tm1_name_filter_for_dimension_exclude_only(self):
        pf = FilterRules(["Dimensions('BW Comp*')"])
        result = pf.to_tm1_name_filter("dimension")
        assert result.filter_expr == "not (startswith(Name, 'BW Comp'))"
        assert result.skip_all is False

    def test_to_tm1_name_filter_skip_all_when_exclude_all(self):
        """Dimensions('*') as exclude sets skip_all=True."""
        pf = FilterRules(["Dimensions('*')"])
        result = pf.to_tm1_name_filter("dimension")
        assert result.filter_expr is not None
        assert result.skip_all is True

    def test_to_tm1_dimension_filter_inherits_force_include_from_child_rules(self):
        pf = FilterRules(
            [
                "Dimensions('*')",
                "!Dimensions('BW Customers Bill To*')/Hierarchies('*')/Elements('(CH) CH AJACCIO*')",
            ]
        )
        result = pf.to_tm1_name_filter("dimension")
        assert result.filter_expr is not None
        assert "startswith(Name, 'BW Customers Bill To')" in result.filter_expr
        assert result.skip_all is False

    def test_to_tm1_hierarchy_name_filter_scopes_to_current_dimension(self):
        pf = FilterRules(
            [
                "Dimensions('*')/Hierarchies('}*')",
                "!Dimensions('Sales')/Hierarchies('Main*')",
                "Dimensions('Finance')/Hierarchies('Fin*')",
            ]
        )

        sales_result = pf.to_tm1_hierarchy_name_filter("Sales")
        assert sales_result.filter_expr == "(not (startswith(Name, '}'))) or (startswith(Name, 'Main'))"
        assert sales_result.skip_all is False
        marketing_result = pf.to_tm1_hierarchy_name_filter("Marketing")
        assert marketing_result.filter_expr == "not (startswith(Name, '}'))"
        assert marketing_result.skip_all is False

    def test_to_tm1_hierarchy_name_filter_inherits_force_include_from_element_rules(self):
        pf = FilterRules(
            [
                "Dimensions('Sales')/Hierarchies('Main*')",
                "Dimensions('Sales')/Hierarchies('Main*')/Elements('X*')",
                "!Dimensions('Sales')/Hierarchies('LeafOnly')/Elements('LeafA')",
            ]
        )

        result = pf.to_tm1_hierarchy_name_filter("Sales")
        assert result.filter_expr is not None
        assert "not (startswith(Name, 'Main'))" in result.filter_expr
        assert "Name eq 'LeafOnly'" in result.filter_expr

    def test_to_tm1_element_name_filter_uses_3_level_rules(self):
        """3-level rules (dim/hier/elem) apply when building element filter. ! = include."""
        pf = FilterRules(
            [
                "!Dimensions('Sales')/Hierarchies('Main')/Elements('X*')",
                "Dimensions('Sales')/Hierarchies('Main')/Elements('Total*')",
            ]
        )
        result = pf.to_tm1_element_name_filter("Sales", "Main")
        assert result.filter_expr == "(not (startswith(Name, 'Total'))) or (startswith(Name, 'X'))"
        assert result.skip_all is False

    def test_to_tm1_element_name_filter_ignores_2_level_rules(self):
        """2-level rules (dim/hier) do not affect element filter."""
        pf = FilterRules(
            [
                "Dimensions('Sales')/Hierarchies('Main*')",
            ]
        )
        result = pf.to_tm1_element_name_filter("Sales", "Main")
        assert result.filter_expr is None

    def test_to_tm1_subset_name_filter_uses_3_level_rules(self):
        """3-level rules (dim/hier/subset) apply when building subset filter. ! = include."""
        pf = FilterRules(
            [
                "!Dimensions('Sales')/Hierarchies('Main')/Subsets('Default*')",
            ]
        )
        result = pf.to_tm1_subset_name_filter("Sales", "Main")
        assert result.filter_expr == "startswith(Name, 'Default')"
        assert result.skip_all is False

    def test_to_tm1_child_name_filter_with_parent_chain(self):
        """to_tm1_child_name_filter accepts parent_chain for 3-level. ! = include."""
        pf = FilterRules(
            [
                "!Dimensions('Sales')/Hierarchies('Main')/Elements('X*')",
            ]
        )
        result = pf.to_tm1_child_name_filter(
            parent_chain=[
                (EntityType.DIMENSION, "Sales"),
                (EntityType.HIERARCHY, "Main"),
            ],
            child_entity_type=EntityType.ELEMENT,
        )
        assert result.filter_expr == "startswith(Name, 'X')"

    def test_to_tm1_name_filter_multiple_excludes_are_anded(self):
        pf = FilterRules(
            [
                "Dimensions('BW*')",
                "Dimensions('*Comp')",
            ]
        )

        result = pf.to_tm1_name_filter("dimension")
        assert result.filter_expr == "(not (startswith(Name, 'BW'))) and (not (endswith(Name, 'Comp')))"
        assert result.skip_all is False

    def test_to_tm1_edge_name_filter_parent_component_format(self):
        """Edge rules use Edges('parentName'/'componentName') format."""
        pf = FilterRules(
            [
                "Dimensions('Sales')/Hierarchies('Main')/Edges('Total*'/'*')",
                "!Dimensions('Sales')/Hierarchies('Main')/Edges('*'/'Leaf*')",
            ]
        )
        result = pf.to_tm1_edge_name_filter("Sales", "Main")
        assert "ParentName" in result.filter_expr
        assert "ComponentName" in result.filter_expr
        assert "Total" in result.filter_expr
        assert "Leaf" in result.filter_expr
        assert result.skip_all is False

    def test_to_tm1_edge_name_filter_wildcard_all(self):
        """Edges('*') as exclude rule sets skip_all=True (no TM1 call needed)."""
        pf = FilterRules(
            ["Dimensions('*')/Hierarchies('*')/Edges('*')"]  # exclude all edges
        )
        result = pf.to_tm1_edge_name_filter("Dim", "Hier")
        assert result.filter_expr is not None
        assert result.skip_all is True

    def test_to_tm1_filter_skip_all_false_when_include_present(self):
        """skip_all is False when any include rule exists."""
        pf = FilterRules(
            ["!Dimensions('*')/Hierarchies('*')/Edges('*')"]  # include all
        )
        result = pf.to_tm1_edge_name_filter("Dim", "Hier")
        assert result.skip_all is False

    def test_filter_rules_raises_on_invalid_rule_when_strict(self):
        """When raise_on_invalid_rule=True, invalid rules raise ValueError."""
        with pytest.raises(ValueError, match="does not match any entity pattern"):
            FilterRules(
                ["Dimensions('BW*')", "InvalidRule('x')"],
                raise_on_invalid_rule=True,
            )

    def test_filter_rules_skips_invalid_rule_when_not_strict(self):
        """When raise_on_invalid_rule=False, invalid rules are silently skipped."""
        pf = FilterRules(
            ["Dimensions('BW*')", "InvalidRule('x')"],
            raise_on_invalid_rule=False,
        )
        assert pf.get_rules_for_entity("dimension") == ["Dimensions('BW*')"]


class TestChangeset:

    def test_apply_uses_sorted_order_for_delete(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch deletes so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give delete something with a .url so apply() doesn't fail
        def delete_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"DELETE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_delete.side_effect = delete_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert delete order ---
        deleted_types = [
            type(call.kwargs["object_instance"])
            for call in mock_delete.call_args_list
        ]

        # For deletes, precedence is:
        # mdx_views -> rules -> cubes -> edges -> elements -> subsets -> hierarchies -> dimensions -> chore -> process
        assert deleted_types == [MDXView, Cube, Edge, Element, Chore, Process]


    def test_apply_uses_sorted_order_for_create(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch creates so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give create something with a .url so apply() doesn't fail
        def create_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"CREATE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_create.side_effect = create_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert create order ---
        created_types = [
            type(call.kwargs["object_instance"])
            for call in mock_create.call_args_list
        ]

        # For creates, precedence is:
        # dimensions -> hierarchies -> subsets -> elements -> edges -> cubes -> mdx_views -> rules -> processes -> chores
        assert created_types == [Subset, Element, Element, Edge, Edge, MDXView]


    def test_apply_uses_sorted_order_for_update(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch update so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.apply.update_object")

        # Give update something with a .url so apply() doesn't fail
        def update_side_effect(**kwargs):
            obj = kwargs["object_instance"]
            return types.SimpleNamespace(url=f"UPDATE:{obj.__class__}:{obj.name}", status_code=200, ok=True)

        mock_update.side_effect = update_side_effect

        tm1_service = mocker.Mock()

        success, _ = apply(tm1_service=tm1_service, changeset=changeset, fail_fast=False)
        assert success

        # --- Assert update order ---
        updated_types = [
            type(call.kwargs["object_instance"])
            for call in mock_update.call_args_list
        ]

        # For updates, precedence is:
        # subsets -> mdx_views -> unified rules -> processes -> chores
        assert updated_types == [Subset, MDXView, Rule, Process, Chore]

    def test_apply_skips_changes_marked_apply_false(self, mocker):
        changeset = Changeset(changeset_id="20260413000001")
        process_a = make_process(name="ProcApply")
        process_b = make_process(name="ProcSkip")
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_a.uri(),
                body=process_a,
                apply=True,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_b.uri(),
                body=process_b,
                apply=False,
            ),
        ]

        mock_create = mocker.patch("tm1_git_py.apply.create_object")
        mocker.patch("tm1_git_py.apply.delete_object")
        mocker.patch("tm1_git_py.apply.update_object")
        mock_create.return_value = types.SimpleNamespace(url="ok", status_code=200, ok=True)

        success, _ = apply(tm1_service=mocker.Mock(), changeset=changeset, fail_fast=False)

        assert success
        assert mock_create.call_count == 1
        assert mock_create.call_args.kwargs["object_instance"].name == "ProcApply"

    def test_changeset_persist_creates_sqlite_with_expected_name(self):
        changeset = Changeset(changeset_id="20260413000002")
        process_obj = make_process(name="ProcPersist")
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
            )
        ]

        sqlite_path = changeset.sqlite_path
        assert sqlite_path.name == "changeset-20260413000002.sqlite"
        assert sqlite_path.exists()

        conn = sqlite3.connect(sqlite_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM changes").fetchone()
            assert int(row[0]) == 1
        finally:
            conn.close()

    def test_changeset_filter_uses_readme_rules_and_preserves_parent_exclude(self):
        changeset = Changeset(changeset_id="20260413000003")
        dim = make_dimension(name="Sales", hierarchy_names=["Main"])
        subset_obj = make_subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([Sales].[Main])}",
            dimension_name="Sales",
            hierarchy_name="Main",
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.DIMENSION,
                uri=dim.uri(),
                body=dim,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=subset_obj.uri("Sales", "Main"),
                body=subset_obj,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
            ),
        ]

        toggled = changeset.filter(["Dimensions('Sales')"])
        assert toggled == 2
        changes = changeset.query(from_=0, to=10)
        by_uri = {change.uri: change.apply for change in changes}
        assert by_uri[dim.uri()] is False
        assert by_uri[subset_obj.uri("Sales", "Main")] is False
        assert by_uri[process_obj.uri()] is True

    def test_changeset_query_supports_filter_and_paging(self):
        changeset = Changeset(changeset_id="20260413000004")
        processes = [make_process(name=f"Proc{i}") for i in range(6)]
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
            )
            for process_obj in processes
        ]

        page = changeset.query(
            rules=["Processes('Proc0')", "Processes('Proc4')"],
            offset=1,
            limit=2,
        )
        assert [change.body.name for change in page] == ["Proc2", "Proc3"]

    def test_changeset_filter_preserves_apply_when_rule_does_not_match(self):
        changeset = Changeset(changeset_id="20260413000006")
        p0 = make_process(name="Proc0")
        p1 = make_process(name="Proc1")
        changeset.changes = [
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p0.uri(), body=p0, apply=False),
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p1.uri(), body=p1, apply=True),
        ]

        updated = changeset.filter(["Processes('Proc1')"])

        assert updated == 1
        queried = changeset.query(from_=0, to=10)
        by_name = {change.body.name: change.apply for change in queried}
        assert by_name["Proc0"] is False
        assert by_name["Proc1"] is False

    def test_changeset_filter_unignores_only_matching_rules(self):
        changeset = Changeset(changeset_id="20260413000008")
        p0 = make_process(name="Proc0")
        p1 = make_process(name="Proc1")
        changeset.changes = [
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p0.uri(), body=p0, apply=False),
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p1.uri(), body=p1, apply=False),
        ]

        updated = changeset.filter(["!Processes('Proc1')"])

        assert updated == 1
        queried = changeset.query(from_=0, to=10)
        by_name = {change.body.name: change.apply for change in queried}
        assert by_name["Proc0"] is False
        assert by_name["Proc1"] is True

    def test_changeset_filter_with_no_rules_preserves_existing_apply_state(self):
        changeset = Changeset(changeset_id="20260413000009")
        p0 = make_process(name="Proc0")
        p1 = make_process(name="Proc1")
        changeset.changes = [
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p0.uri(), body=p0, apply=False),
            Change(change_type=ChangeType.ADD, object_type=ObjectType.PROCESS, uri=p1.uri(), body=p1, apply=True),
        ]

        updated = changeset.filter([])

        assert updated == 0
        queried = changeset.query(from_=0, to=10)
        by_name = {change.body.name: change.apply for change in queried}
        assert by_name["Proc0"] is False
        assert by_name["Proc1"] is True

    def test_changeset_filter_force_include_dimension_cascades_to_descendants(self):
        changeset = Changeset(changeset_id="20260413000010")
        hierarchy_obj = Hierarchy(name="Main", elements=[], edges=[], subsets=[])
        dimension_obj = Dimension(
            name="Sales",
            hierarchies=[hierarchy_obj],
            defaultHierarchy=hierarchy_obj,
        )
        subset_obj = make_subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([Sales].[Main])}",
            dimension_name="Sales",
            hierarchy_name="Main",
        )
        element_obj = make_element("Leaf1")
        edge_obj = Edge(parent="Total", component_name="Leaf1", weight=1)

        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.DIMENSION,
                uri=dimension_obj.uri(),
                body=dimension_obj,
                apply=False,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.HIERARCHY,
                uri=hierarchy_obj.uri("Sales"),
                body=hierarchy_obj,
                apply=False,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=subset_obj.uri("Sales", "Main"),
                body=subset_obj,
                apply=False,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.ELEMENT,
                uri=element_obj.uri("Sales", "Main"),
                body=element_obj,
                apply=False,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.EDGE,
                uri=edge_obj.uri("Sales", "Main"),
                body=edge_obj,
                apply=False,
            ),
        ]

        updated = changeset.filter(["!Dimensions('*')"])

        assert updated == 5
        queried = changeset.query(from_=0, to=20)
        by_uri = {change.uri: change.apply for change in queried}
        assert by_uri[dimension_obj.uri()] is True
        assert by_uri[hierarchy_obj.uri("Sales")] is True
        assert by_uri[subset_obj.uri("Sales", "Main")] is True
        assert by_uri[element_obj.uri("Sales", "Main")] is True
        assert by_uri[edge_obj.uri("Sales", "Main")] is True

    def test_changeset_can_be_loaded_by_changeset_id(self):
        changeset = Changeset(changeset_id="20260413000007")
        process_obj = make_process(name="ProcLoad")
        changeset.changes.append(
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
            )
        )
        loaded = Changeset.from_changeset_id("20260413000007")
        assert len(loaded.changes) == 1
        assert loaded.changes[0].body.name == "ProcLoad"

    def test_changeset_from_changeset_id_raises_when_missing(self):
        with pytest.raises(FileNotFoundError):
            Changeset.from_changeset_id("20990101000000")


    def test_models_expose_canonical_urls(self):
        hierarchy_obj = make_hierarchy(dimension_name="TestDim", hierarchy_name="TestHier")
        dimension_obj = Dimension(
            name="TestDim",
            hierarchies=[hierarchy_obj],
            defaultHierarchy=hierarchy_obj,
        )
        subset_obj = make_subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([TestDim].[TestHier])}",
            dimension_name="TestDim",
            hierarchy_name="TestHier",
        )
        process_obj = make_process(name="ProcURL")
        chore_obj = make_chore(name="ChoreURL")
        view_obj = MDXView(name="ViewURL", mdx="SELECT FROM [MockCube]")
        rule_obj = Rule(area="[default]", full_statement="[default]=N:1;")
        cube_obj = Cube(
            name="MockCube",
            dimensions=[dimension_obj],
            rules=[rule_obj],
            views=[view_obj],
        )

        assert cube_obj.uri() == "Cubes('MockCube')"
        assert rule_obj.uri("MockCube") == "Cubes('MockCube')/Rules('default')"
        assert view_obj.uri("MockCube") == "Cubes('MockCube')/Views('ViewURL')"
        assert dimension_obj.uri() == "Dimensions('TestDim')"
        assert hierarchy_obj.uri("TestDim") == "Dimensions('TestDim')/Hierarchies('TestHier')"
        assert subset_obj.uri("TestDim", "TestHier") == "Dimensions('TestDim')/Hierarchies('TestHier')/Subsets('SubsetA')"
        assert process_obj.uri() == "Processes('ProcURL')"
        assert chore_obj.uri() == "Chores('ChoreURL')"

    def test_model_filter_uses_urls_baseline(self):
        rule_a = Rule(area="[A]", full_statement="[A]=N:1;")
        rule_b = Rule(area="[B]", full_statement="[B]=N:2;")
        view_obj = MDXView(name="KeepView", mdx="SELECT FROM [MockCube]")
        dim_obj = make_dimension(name="MockDim", hierarchy_names=[])
        cube_obj = Cube(
            name="MockCube",
            dimensions=[dim_obj],
            rules=[rule_a, rule_b],
            views=[view_obj],
        )
        model = Model(cubes=[cube_obj], dimensions=[dim_obj], processes=[make_process("ProcA")], chores=[make_chore("ChoreA")])

        rules_filtered = filter_module.filter(model, ["Cubes('MockCube')/Rules('default')"])
        assert len(rules_filtered.cubes) == 1
        assert rules_filtered.cubes[0].name == "MockCube"
        assert len(rules_filtered.cubes[0].rules) == 0

        cubes_filtered = filter_module.filter(model, ["Cubes('MockCube')"])
        assert len(cubes_filtered.cubes) == 0

    def test_model_filter_excludes_leaves_hierarchy_by_default(self):
        main_hierarchy = Hierarchy(
            name="Main",
            elements=[],
            edges=[],
            subsets=[],
        )
        leaves_hierarchy = Hierarchy(
            name="Leaves",
            elements=[],
            edges=[],
            subsets=[],
        )
        dimension = Dimension(
            name="MockDim",
            hierarchies=[main_hierarchy, leaves_hierarchy],
            defaultHierarchy=main_hierarchy,
        )
        model = Model(cubes=[], dimensions=[dimension], processes=[], chores=[])

        filtered = filter_module.filter(model, [])

        assert [hier.name for hier in filtered.dimensions[0].hierarchies] == ["Main"]

    def test_model_filter_force_include_leaves_hierarchy_overrides_default_exclude(self):
        main_hierarchy = Hierarchy(
            name="Main",
            elements=[],
            edges=[],
            subsets=[],
        )
        leaves_hierarchy = Hierarchy(
            name="Leaves",
            elements=[],
            edges=[],
            subsets=[],
        )
        dimension = Dimension(
            name="MockDim",
            hierarchies=[main_hierarchy, leaves_hierarchy],
            defaultHierarchy=main_hierarchy,
        )
        model = Model(cubes=[], dimensions=[dimension], processes=[], chores=[])

        filtered = filter_module.filter(model, ["!Dimensions('*')/Hierarchies('Leaves')"])

        assert [hier.name for hier in filtered.dimensions[0].hierarchies] == [
            "Main",
            "Leaves",
        ]


    def test_export_persists_expected_payload(self, tmp_path):
        changes = Changeset(changeset_id="20260413000000")

        created_subset = make_subset(
            name="Subset_Create",
            expression="{[Dim_New].[Hier_New].Members}",
            dimension_name="Dim_New",
            hierarchy_name="Hier_New",
        )
        removed_view = make_mdx_view(
            name="View_To_Delete",
            mdx="SELECT FROM [Cube_One]",
            source_path="cubes/Cube_One.views/View_To_Delete.json",
        )
        new_dimension = make_dimension(
            name="Dim_Update",
            hierarchy_names=["Base", "Added"],
            source_path="/dimensions/Dim_Update",
        )

        changes.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("Dim_New", "Hier_New", "Subset_Create"),
                body=created_subset,
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("Dim_Update"),
                body=new_dimension,
            ),
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.MDX_VIEW,
                uri=MDXView.uri_for("Cube_One", "View_To_Delete"),
                body=removed_view,
            ),
        ]

        export_path = tmp_path / "changes.yml"
        changes.export(export_path)

        exported_payload = yaml.safe_load(export_path.read_text(encoding="utf-8"))

        expected_payload = {
            "changeset_id": "20260413000000",
            "summary": {
                "add": 1,
                "remove": 1,
                "modify": 1,
            },
            "changes": [
                {
                    "change_type": "remove",
                    "object_type": "MDXView",
                    "uri": MDXView.uri_for("Cube_One", "View_To_Delete"),
                    "apply": True,
                    "body": {
                        "Name": "View_To_Delete",
                    },
                },
                {
                    "change_type": "add",
                    "object_type": "Subset",
                    "uri": Subset.uri_for("Dim_New", "Hier_New", "Subset_Create"),
                    "apply": True,
                    "body": {
                        "Name": "Subset_Create",
                        "Expression": "{[Dim_New].[Hier_New].Members}",
                    },
                },
                {
                    "change_type": "modify",
                    "object_type": "Dimension",
                    "uri": Dimension.uri_for("Dim_Update"),
                    "apply": True,
                    "body": {
                        "Name": "Dim_Update",
                        "Hierarchies": [
                            "dimensions/Dim_Update.hierarchies/Base.json",
                            "dimensions/Dim_Update.hierarchies/Added.json",
                        ],
                        "DefaultHierarchy": "dimensions/Dim_Update.hierarchies/Base.json",
                    },
                },
            ],
        }

        exported_payload_pretty = json.dumps(exported_payload, sort_keys=True, indent=2)
        expected_payload_pretty = json.dumps(expected_payload, sort_keys=True, indent=2)
        assert exported_payload_pretty == expected_payload_pretty


    def test_import_changeset(self, tmp_path):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = tm1_git_py.Comparator()

        changeset_compared = comparator.compare(model_old, model_new)
        export_path = tmp_path / "changes_exported.yaml"
        changeset_compared.export(file_path=export_path)

        changeset_imported = import_changeset(
            changeset_file=str(export_path)
        )

        for expected, actual in zip(changeset_compared.changes, changeset_imported.changes):
            assert expected.change_type == actual.change_type
            assert expected.object_type == actual.object_type
            assert expected.uri == actual.uri
            assert expected.body.__class__ == actual.body.__class__

    def test_changeset_class_import_alias_json_stream(self, tmp_path):
        changeset = Changeset(changeset_id="20260413000008")
        process_obj = make_process(name="ProcAlias")
        changeset.changes.append(
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
                apply=False,
            )
        )
        export_path = tmp_path / "alias_import.json"
        changeset.export(export_path)

        imported = import_changeset(export_path)
        assert imported.changeset_id == "20260413000008"
        assert len(imported.changes) == 1
        assert imported.changes[0].apply is False

    def test_export_import_roundtrip_preserves_changeset_id_and_apply(self, tmp_path):
        changeset = Changeset(changeset_id="20260413000005")
        process_obj = make_process(name="ProcRoundtrip")
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=process_obj.uri(),
                body=process_obj,
                apply=False,
            )
        ]

        export_path = tmp_path / "roundtrip_changeset.yaml"
        changeset.export(export_path)

        imported = import_changeset(str(export_path))

        assert imported.changeset_id == "20260413000005"
        assert len(imported.changes) == 1
        assert imported.changes[0].apply is False

    def test_export_remove_edge_body_uses_parent_component_weight(self, tmp_path):
        changeset = Changeset(changeset_id="20260416000003")
        edge = Edge(parent="DimElemC", component_name="DimElem1", weight=1)
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.EDGE,
                uri="Dimensions('TestDimMultiHier')/Hierarchies('TestDimMultiHier')/Edges('DimElemC'/'DimElem1')",
                body=edge,
            )
        ]

        export_path = tmp_path / "remove_edge_payload.yml"
        changeset.export(export_path)
        payload = yaml.safe_load(export_path.read_text(encoding="utf-8"))
        body = payload["changes"][0]["body"]
        assert body == {
            "ParentName": "DimElemC",
            "ComponentName": "DimElem1",
            "Weight": 1,
        }

    def test_import_rejects_edge_body_legacy_name_format(self, tmp_path):
        changeset = Changeset(changeset_id="20260416000001")
        edge = Edge(parent="DimElemC", component_name="DimElem1", weight=1)
        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.EDGE,
                uri="Dimensions('TestDimMultiHier')/Hierarchies('TestDimMultiHier')/Edges('DimElemC'/'DimElem1')",
                body=edge,
            )
        ]
        path = tmp_path / "edge_remove_legacy_name.yml"
        changeset.export(path)
        raw = path.read_text(encoding="utf-8")
        raw = raw.replace("ParentName: DimElemC\n", "")
        raw = raw.replace("ComponentName: DimElem1\n", "name: DimElemC:DimElem1\n")
        path.write_text(raw, encoding="utf-8")

        imported = import_changeset(path)
        assert len(imported.changes) == 0

    def test_export_changeset_preserves_unicode_characters(self, tmp_path):
        changes = Changeset()
        subset = make_subset(
            name="Subset_Día",
            expression="{[Dim].[Hier].[café]}",
            dimension_name="Dim",
            hierarchy_name="Hier",
        )
        changes.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("Dim", "Hier", "Subset_Día"),
                body=subset,
            )
        ]

        export_path = tmp_path / "unicode_changes.yml"
        changes.export(export_path)
        raw_yaml = export_path.read_text(encoding="utf-8")

        assert "Subset_Día" in raw_yaml
        assert "café" in raw_yaml
        assert "\\x" not in raw_yaml


class TestChangesetFiltering:

    def test_filter_changeset_removes_parent_and_children_across_sections(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        subset_mod_old = Subset(
            name="SubsetMod",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        subset_mod_new = Subset(
            name="SubsetMod",
            expression="{[MockDim].[MockHier].[E1]}",
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                uri=Hierarchy.uri_for("MockDim", "MockHier"),
                body=hier_new
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetMod"),
                body=subset_mod_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=Process.uri_for("KeepProcess"),
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": [Dimension.uri_for("MockDim")],
                "modify": [
                    Hierarchy.uri_for("MockDim", "MockHier")
                ],
            },
            filter_children=True
        )

        assert [obj.body.name for obj in filtered.changes] == ["SubsetA", "KeepProcess"]



    def test_filter_changeset_keeps_parent_when_only_child_matches(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )

        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [Subset.uri_for("MockDim", "MockHier", "SubsetA")],
                "remove": [],
                "modify": [],
            }
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        assert [obj.name for obj in filtered_adds] == ["MockDim"]


    def test_filter_changeset_does_not_remove_children_when_filter_children_false(self):
        changeset = Changeset()
        dim = make_dimension(name="MockDim", source_path="dimensions/MockDim")
        hier_new = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        hier_old = make_hierarchy(dimension_name="MockDim", hierarchy_name="MockHier")
        subset = Subset(
            name="SubsetA",
            expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        )
        process_obj = make_process(name="KeepProcess")

        changeset.changes = [
            Change(
                change_type=ChangeType.REMOVE,
                object_type=ObjectType.DIMENSION,
                uri=Dimension.uri_for("MockDim"),
                body=dim
            ),
            Change(
                change_type=ChangeType.MODIFY,
                object_type=ObjectType.HIERARCHY,
                uri=Hierarchy.uri_for("MockDim", "MockHier"),
                body=hier_new
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.SUBSET,
                uri=Subset.uri_for("MockDim", "MockHier", "SubsetA"),
                body=subset
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=Process.uri_for("KeepProcess"),
                body=process_obj
            ),
        ]

        filtered = filter_changeset(
            changeset,
            {
                "add": [],
                "remove": [Dimension.uri_for("MockDim")],
                "modify": [],
            },
            filter_children=False
        )

        filtered_adds = [c.body for c in filtered.changes if c.change_type == ChangeType.ADD]
        filtered_mods = [c.body for c in filtered.changes if c.change_type == ChangeType.MODIFY]
        filtered_rems = [c.body for c in filtered.changes if c.change_type == ChangeType.REMOVE]

        assert [obj.name for obj in filtered_adds] == ["SubsetA", "KeepProcess"]
        assert len(filtered_mods) == 1
        assert [obj.name for obj in filtered_rems] == []



class TestSubsetCRUD:

    def test_create_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].Members}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1py_subset_cls = mocker.patch("tm1_git_py.model.subset.TM1py.Subset")
        tm1py_subset_instance = tm1py_subset_cls.return_value
        tm1_service.subsets.create.return_value = "create-result"

        result = subset.create_subset(
            tm1_service,
            subset_mock,
            uri=Subset.uri_for("Dim_A", "Hier_A", "Subset_A"),
        )

        tm1py_subset_cls.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
            expression="{[Dim_A].[Hier_A].Members}",
        )
        tm1_service.subsets.create.assert_called_once_with(tm1py_subset_instance)
        assert result == "create-result"

    def test_delete_subset_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        subset_mock = make_subset(
            name="Subset_Delete",
            expression="{[Dim_Del].[Hier_Del].Members}",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )

        tm1_service.subsets.delete.return_value = "delete-result"

        result = subset.delete_subset(
            tm1_service,
            subset_mock,
            uri=Subset.uri_for("Dim_Del", "Hier_Del", "Subset_Delete"),
        )

        tm1_service.subsets.delete.assert_called_once_with(
            subset_name="Subset_Delete",
            dimension_name="Dim_Del",
            hierarchy_name="Hier_Del",
        )
        assert result == "delete-result"


    def test_update_subset_updates_expression_and_calls_tm1(self, mocker):
        tm1_service = mocker.Mock()

        subset_new = make_subset(
            name="Subset_A",
            expression="{[Dim_A].[Hier_A].NewMembers}",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        tm1_subset_obj = mocker.Mock()
        tm1_subset_obj.expression = "{[Dim_A].[Hier_A].OldMembers}"
        tm1_service.subsets.get.return_value = tm1_subset_obj

        tm1_service.subsets.update.return_value = "update-result"

        result = subset.update_subset(
            tm1_service,
            subset_new,
            uri=Subset.uri_for("Dim_A", "Hier_A", "Subset_A"),
        )

        tm1_service.subsets.get.assert_called_once_with(
            subset_name="Subset_A",
            dimension_name="Dim_A",
            hierarchy_name="Hier_A",
        )

        assert tm1_subset_obj.expression == "{[Dim_A].[Hier_A].NewMembers}"
        tm1_service.subsets.update.assert_called_once_with(tm1_subset_obj)
        assert result == "update-result"



class TestEdgeCRUD:

    def test_create_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_A",
            component_name="Child_A",
            weight=1,
        )
        tm1_service.elements.add_edges.return_value = "create-result"

        result = edge.create_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_A", "Hier_A", "Parent_A", "Child_A"),
        )

        tm1_service.elements.add_edges.assert_called_once_with(
            "Hier_A",
            "Dim_A",
            {("Parent_A", "Child_A"): 1},
        )
        assert result == "create-result"


    def test_update_edge_fetches_hierarchy_and_updates_edge(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_B",
            component_name="Child_B",
            weight=2,
        )

        hierarchy_object = mocker.Mock()
        tm1_service.hierarchies.get.return_value = hierarchy_object
        tm1_service.hierarchies.update.return_value = "update-result"

        result = edge.update_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_B", "Hier_B", "Parent_B", "Child_B"),
        )

        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Dim_B",
            hierarchy_name="Hier_B",
        )
        hierarchy_object.update_edge.assert_called_once_with(
            parent="Parent_B",
            component="Child_B",
            weight=2,
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy_object)
        assert result == "update-result"


    def test_delete_edge_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        edge_obj = Edge(
            parent="Parent_C",
            component_name="Child_C",
            weight=3,
        )
        tm1_service.elements.remove_edge.return_value = "delete-result"

        result = edge.delete_edge(
            tm1_service,
            edge_obj,
            uri=Edge.uri_for("Dim_C", "Hier_C", "Parent_C", "Child_C"),
        )

        tm1_service.elements.remove_edge.assert_called_once_with(
            "Hier_C",
            "Dim_C",
            "Parent_C",
            "Child_C",
        )
        assert result == "delete-result"



class TestElementCRUD:

    def test_create_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_A",
            type="Numeric",
        )

        tm1py_element_cls = mocker.patch("tm1_git_py.model.element.TM1py.Element")
        tm1py_element_instance = tm1py_element_cls.return_value
        tm1_service.elements.create.return_value = "create-result"

        result = element.create_element(
            tm1_service,
            element_obj,
            uri=Element.uri_for("Dim_A", "Hier_A", "Elem_A"),
        )

        tm1py_element_cls.assert_called_once_with(name="Elem_A", element_type="Numeric")
        tm1_service.elements.create.assert_called_once_with(
            hierarchy_name="Hier_A",
            dimension_name="Dim_A",
            element=tm1py_element_instance,
        )
        assert result == "create-result"


    def test_delete_element_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        element_obj = Element(
            name="Elem_B",
            type="String",
        )
        tm1_service.elements.delete.return_value = "delete-result"

        result = element.delete_element(
            tm1_service,
            element_obj,
            uri=Element.uri_for("Dim_B", "Hier_B", "Elem_B"),
        )

        tm1_service.elements.delete.assert_called_once_with(
            hierarchy_name="Hier_B",
            dimension_name="Dim_B",
            element_name="Elem_B",
        )
        assert result == "delete-result"



class TestHierarchyCRUD:

    def test_create_hierarchy_does_not_create_edges_or_elements(self, mocker):
        tm1_service = mocker.Mock()

        elements = [make_element("E1"), make_element("E2")]
        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_A",
            hierarchy_name="Hierarchy_A",
            elements=elements,
            edges=[
                Edge(parent="Total", component_name="E1", weight=1),
                Edge(parent="Total", component_name="E2", weight=2),
            ],
        )

        tm1py_hierarchy_cls = mocker.patch("tm1_git_py.model.hierarchy.TM1py.Hierarchy")
        tm1py_hierarchy_obj = tm1py_hierarchy_cls.return_value

        response = mocker.Mock()
        tm1_service.hierarchies.create.return_value = response
        create_element_mock = mocker.patch("tm1_git_py.model.hierarchy.create_element")

        result = hierarchy.create_hierarchy(
            tm1_service,
            hierarchy_mock,
            uri=Hierarchy.uri_for("Dimension_A", "Hierarchy_A"),
        )

        # Assert: TM1py.Hierarchy constructed with correct name + dimension
        tm1py_hierarchy_cls.assert_called_once_with(
            name="Hierarchy_A",
            dimension_name="Dimension_A",
        )

        # TM1 service called to create hierarchy
        tm1_service.hierarchies.create.assert_called_once_with(tm1py_hierarchy_obj)
        assert result is response

        # create_hierarchy only creates the hierarchy itself now.
        tm1py_hierarchy_obj.add_edge.assert_not_called()
        tm1_service.elements.exists.assert_not_called()
        create_element_mock.assert_not_called()


    def test_delete_hierarchy_calls_tm1_with_correct_dimension_and_name(self, mocker):
        tm1_service = mocker.Mock()

        hierarchy_mock = make_hierarchy(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )

        tm1_service.hierarchies.delete.return_value = "delete-result"

        result = hierarchy.delete_hierarchy(
            tm1_service,
            hierarchy_mock,
            uri=Hierarchy.uri_for("Dimension_X", "Hierarchy_Delete"),
        )

        tm1_service.hierarchies.delete.assert_called_once_with(
            dimension_name="Dimension_X",
            hierarchy_name="Hierarchy_Delete",
        )
        assert result == "delete-result"



class TestDimensionCRUD:

    def test_create_dimension_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        dimension_input = mocker.Mock()
        dimension_input.name = "TestDim"

        tm1py_dimension_cls = mocker.patch("tm1_git_py.model.dimension.TM1py.Dimension")
        tm1py_dimension_instance = tm1py_dimension_cls.return_value
        tm1_service.dimensions.create.return_value = "create-result"

        result = dimension.create_dimension(tm1_service, dimension_input.name)

        tm1py_dimension_cls.assert_called_once_with("TestDim")
        tm1_service.dimensions.create.assert_called_once_with(tm1py_dimension_instance)
        assert result == "create-result"


    def test_delete_dimension_calls_delete_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.dimensions.delete.return_value = "delete-result"
        dim = make_dimension(name="TestDim", source_path="dimensions/TestDim.json")

        result = dimension.delete_dimension(tm1_service, dim)

        tm1_service.dimensions.delete.assert_called_once_with("TestDim")
        assert result == "delete-result"



class TestMDXViewCRUD:

    def test_create_mdx_view_builds_tm1py_mdxview_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        cube_name = "Cube_A"
        tm1py_mdxview_cls = mocker.patch("tm1_git_py.model.mdxview.TM1py.MDXView")
        tm1py_mdxview_instance = tm1py_mdxview_cls.return_value
        tm1_service.views.create.return_value = "create-result"

        result = mdxview.create_mdxview(
            tm1_service,
            mdx_view,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        tm1py_mdxview_cls.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
            MDX="SELECT FROM [Cube_A]",
        )
        tm1_service.views.create.assert_called_once_with(tm1py_mdxview_instance)
        assert result == "create-result"


    def test_delete_mdx_view_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.views.delete.return_value = "delete-result"
        mdx_view = make_mdx_view(
            name="View_A",
            mdx="SELECT FROM [Cube_A]",
        )

        result = mdxview.delete_mdxview(
            tm1_service,
            mdx_view,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        tm1_service.views.delete.assert_called_once_with(view_name=mdx_view.name, cube_name="Cube_A")
        assert result == "delete-result"


    def test_update_mdx_view_updates_mdx_and_calls_update(self, mocker):
        tm1_service = mocker.Mock()
        cube_name = "Cube_A"

        mdx_view_new = make_mdx_view(
            name="View_A",
            mdx="SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]",
        )

        tm1_mdx_view_obj = mocker.Mock()
        tm1_mdx_view_obj.mdx = "OLD MDX"
        tm1_service.views.get_mdx_view.return_value = tm1_mdx_view_obj
        tm1_service.views.update.return_value = "update-result"

        result = mdxview.update_mdxview(
            tm1_service,
            mdx_view_new,
            uri=MDXView.uri_for("Cube_A", "View_A"),
        )

        # Assert: we got the existing MDX view from TM1
        tm1_service.views.get_mdx_view.assert_called_once_with(
            cube_name=cube_name,
            view_name="View_A",
        )

        # The MDX on the TM1 object should be updated to the new MDX
        assert tm1_mdx_view_obj.mdx == "SELECT {[Dim].[Elem]} ON 0 FROM [Cube_A]"

        # And update() should be called with that object
        tm1_service.views.update.assert_called_once_with(tm1_mdx_view_obj)

        # Function returns whatever TM1 update() returned
        assert result == "update-result"



class TestCubeCRUD:

    def test_create_cube_builds_tm1py_cube_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()
        cube_mock = make_cube(
            name="Cube_A",
            dimension_names=["Version", "Period", "Channel"],
        )

        tm1py_cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        tm1py_cube_instance = tm1py_cube_cls.return_value
        tm1_service.cubes.create.return_value = "create-result"

        result = cube.create_cube(tm1_service, cube_mock)

        expected_dims = ["Version", "Period", "Channel"]
        expected_rule_text = cube_mock.get_rule_text()

        tm1py_cube_cls.assert_called_once_with(
            cube_mock.name,
            expected_dims,
            expected_rule_text,
        )
        tm1_service.cubes.create.assert_called_once_with(tm1py_cube_instance)
        assert result == "create-result"


    def test_delete_cube_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.cubes.delete.return_value = "delete-result"
        cube_name = "Cube_To_Delete"
        cube_obj = make_cube(name=cube_name)

        result = cube.delete_cube(tm1_service, cube_obj)

        tm1_service.cubes.delete.assert_called_once_with(cube_name)
        assert result == "delete-result"


    @pytest.mark.skip
    def test_update_cube_updates_rules_when_views_same(self, mocker):
        tm1_service = mocker.Mock()

        dim_names = ["Version", "Period"]

        view = MDXView(
            name="ViewSame",
            mdx="SELECT FROM [Cube_B]",
            source_path="/views/Cube_B/ViewSame.json",
        )
        views = [view]

        rules_old = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 1;",
                comment="// old",
            )
        ]
        rules_new = [
            make_rule(
                area="['n']",
                full_statement="['n'] = N: 2;",
                comment="// new",
            )
        ]

        cube_old = make_cube("Cube_B", dim_names, rules_old, views)
        cube_new = make_cube("Cube_B", dim_names, rules_new, views)

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="some different rules")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        # ACT
        result = cube.update_cube(tm1_service, payload)

        # Rules updated
        new_rule_text = cube_new.get_rule_text()
        assert cube_obj.rules._text == new_rule_text

        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_update_cube_reorders_dimensions_when_order_changes_only(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Cube_Order", ["A", "B", "C"])
        cube_new = make_cube("Cube_Order", ["B", "C", "A"])

        payload = {"old": cube_old, "new": cube_new}

        class RulesObj:
            def __init__(self, body: str):
                self.body = body
                self._text = body

        cube_obj = mocker.Mock()
        cube_obj.rules = RulesObj(body="")
        tm1_service.cubes.get.return_value = cube_obj

        tm1_service.cubes.update.return_value = "update-result"

        result = cube.update_cube(tm1_service, payload)

        # --- Assertions on dimension reordering logic ---
        tm1_service.cubes.get.assert_called_once_with("Cube_Order")

        # Because order changed but set is the same, we must reorder storage dims
        tm1_service.cubes.update_storage_dimension_order.assert_called_once_with(
            cube_name="Cube_Order",
            dimension_names=["B", "C", "A"],
        )

        # Rules should not change (both empty), so no extra logic beyond update()
        tm1_service.cubes.update.assert_called_once_with(cube_obj)
        assert result == "update-result"


    @pytest.mark.skip
    def test_add_dimension_to_cube_uses_first_leaf_and_copies_via_temp_cube(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year", "Region"]

        # --- TM1 mocks ---

        # Hierarchy with one consolidated + one leaf
        hier = mocker.Mock()
        consolidated = mocker.Mock()
        consolidated.name = "Total"
        consolidated.element_type = "Consolidated"
        leaf = mocker.Mock()
        leaf.name = "Leaf1"
        leaf.element_type = "Numeric"
        hier.elements.values.return_value = [consolidated, leaf]
        tm1_service.hierarchies.get.return_value = hier

        # Patch TM1py.Cube
        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")

        # Patch bedrock copy
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        # Patch create/delete cube wrappers
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        # create_dimension / element.create_element should NOT be called here
        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        # --- ACT ---
        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) default element: first leaf, no new element created
        tm1_service.hierarchies.get.assert_called_once_with(
            dimension_name="Region",
            hierarchy_name="Region",
        )
        create_dimension_mock.assert_not_called()
        create_elem_mock.assert_not_called()

        # 2) temp cube creation
        cube_cls.assert_called_once_with(
            name=temp_cube_name,
            dimensions=dims_new,
            rules="",
        )
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp with target_dim_mapping
        assert copy_mock.call_count == 2
        first_call = copy_mock.call_args_list[0]
        first_kwargs = first_call.kwargs

        assert first_kwargs["tm1_service"] is tm1_service
        assert first_kwargs["target_cube_name"] == temp_cube_name
        assert first_kwargs["target_dim_mapping"] == {"Region": "Leaf1"}
        assert first_kwargs["clear_target"] is True
        mdx1 = first_kwargs["data_mdx"]
        assert "[Sales]" in mdx1
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "Region" not in mdx1

        # 3) original cube deleted, cube recreated with new definition
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final (no target_dim_mapping)
        second_call = copy_mock.call_args_list[1]
        second_kwargs = second_call.kwargs

        assert second_kwargs["tm1_service"] is tm1_service
        assert second_kwargs["target_cube_name"] == "Sales"
        assert second_kwargs["clear_target"] is True
        assert "target_dim_mapping" not in second_kwargs
        mdx2 = second_kwargs["data_mdx"]
        assert "[Sales__tmp_add_dims]" in mdx2
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "TM1SUBSETALL([Region])" in mdx2

        # 5) temp cube deletion
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip(reason="Ignored per user request")
    def test_add_dimension_to_cube_creates_default_leaf_when_no_leaf_exists(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version"]
        dims_new = ["Version", "NewDim"]

        # Hierarchy with only consolidated elements (no leaves)
        hier = mocker.Mock()
        cons = mocker.Mock()
        cons.name = "Total"
        cons.element_type = "Consolidated"
        hier.elements.values.return_value = [cons]
        tm1_service.hierarchies.get.return_value = hier

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        create_dimension_mock = mocker.patch("tm1_git_py.model.cube.create_dimension")
        create_elem_mock = mocker.patch("tm1_git_py.model.cube.element.create_element")

        cube._add_dimensions_to_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        temp_cube_name = "Sales__tmp_add_dims"

        # 1) dimension created, then hierarchy default element created
        create_dimension_mock.assert_called_once_with(
            tm1_service=tm1_service,
            dimension="NewDim",
        )

        tm1_service.hierarchies.get.assert_called_with(
            dimension_name="NewDim",
            hierarchy_name="NewDim",
        )

        create_elem_mock.assert_called_once()
        elem_kwargs = create_elem_mock.call_args.kwargs
        elem_attributes = elem_kwargs["element"].body_as_dict
        assert elem_kwargs["dimension_name"] == "NewDim"
        assert elem_kwargs["hierarchy_name"] == "NewDim"
        assert elem_kwargs["element"].name == "Legacy Data"
        assert elem_attributes["Type"] == "Numeric"

        # hierarchy should be updated with the new element
        hier.add_element.assert_called_once_with(
            element_name="Legacy Data",
            element_type="Numeric",
        )
        tm1_service.hierarchies.update.assert_called_once_with(hierarchy=hier)

        # 2) temp cube created with new dimensions
        assert cube_cls.call_count == 2

        # First call should be for the temp cube, using keyword args
        temp_call = cube_cls.call_args_list[0]
        assert temp_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }

        # 3) first copy uses 'Legacy Data' as target_dim_mapping
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_dim_mapping"] == {"NewDim": "Legacy Data"}


    @pytest.mark.skip
    def test_add_dimension_to_cube_raises_on_cube_name_mismatch(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales_Old")
        cube_new = make_cube("Sales_New")

        with pytest.raises(ValueError) as excinfo:
            cube._add_dimensions_to_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=["Version"],
                dims_new=["Version", "Region"],
            )

        assert "Cube name mismatch" in str(excinfo.value)

        tm1_service.cubes.create.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_sum_all_default_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year", "Region"]
        dims_new = ["Version", "Year"]

        # TM1: temp cube does not exist yet
        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=None,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # 1) temp cube created with reduced dims
        assert cube_cls.call_count >= 1
        first_cube_call = cube_cls.call_args_list[0]
        assert first_cube_call.kwargs == {
            "name": temp_cube_name,
            "dimensions": dims_new,
            "rules": "",
        }
        tm1_service.cubes.create.assert_called_once_with(cube_cls.return_value)

        # 2) first data_copy_intercube: old -> temp
        assert copy_mock.call_count == 2
        first_call_kwargs = copy_mock.call_args_list[0].kwargs

        assert first_call_kwargs["tm1_service"] is tm1_service
        assert first_call_kwargs["target_cube_name"] == temp_cube_name
        # sum_all => no explicit source_dim_mapping
        assert first_call_kwargs.get("source_dim_mapping") is None
        assert first_call_kwargs["clear_target"] is True
        assert first_call_kwargs["sum_numeric_duplicates"] is True

        mdx1 = first_call_kwargs["data_mdx"]
        # All deleted dims use TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert "FILTER(" not in mdx1  # no keep_by_attr filters here

        # 3) original cube deleted & recreated
        delete_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube_name="Sales",
        )
        create_cube_mock.assert_called_once_with(
            tm1_service=tm1_service,
            cube=cube_new,
        )

        # 4) second data_copy_intercube: temp -> final
        second_call_kwargs = copy_mock.call_args_list[1].kwargs
        assert second_call_kwargs["target_cube_name"] == "Sales"
        assert second_call_kwargs["clear_target"] is True
        assert second_call_kwargs["sum_numeric_duplicates"] is True
        mdx2 = second_call_kwargs["data_mdx"]
        # Now only new dims appear
        assert "TM1SUBSETALL([Version])" in mdx2
        assert "TM1SUBSETALL([Year])" in mdx2
        assert "Region" not in mdx2

        # 5) temp cube deleted at the end
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Year"]

        strategies = {
            "Version": {
                "strategy": "keep_element",
                "element": "Actual",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"

        # temp cube created as before
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        # first bedrock call: old -> temp
        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_element => using source_dim_mapping for Version
        assert first_kwargs["source_dim_mapping"] == {"Version": "Actual"}
        assert first_kwargs["sum_numeric_duplicates"] is True

        # MDX still uses TM1SUBSETALL for Version; filtering is handled by source_dim_mapping
        mdx1 = first_kwargs["data_mdx"]
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "FILTER(" not in mdx1

        # clean-up flow same as sum_all
        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.skip
    def test_delete_dimensions_keep_element_requires_element(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Version"]
        dims_new = []

        strategies = {
            "Version": {
                "strategy": "keep_element",
                # 'element' missing on purpose
            }
        }

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires an 'element' key" in str(excinfo.value)
        # Must not call bedrock if config is invalid
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_strategy(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Region", "Year"]
        dims_new = ["Version", "Year"]

        strategies = {
            "Region": {
                "strategy": "keep_by_attr",
                "attr_name": "KeepOnDrop",
                "attr_value": "Y",
            }
        }

        tm1_service.cubes.exists.return_value = False

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
            strategies=strategies,
            default_strategy="sum_all",
        )

        temp_cube_name = "Sales__tmp_del_multi"
        cube_cls.assert_called()
        tm1_service.cubes.create.assert_called_once()

        first_kwargs = copy_mock.call_args_list[0].kwargs
        assert first_kwargs["target_cube_name"] == temp_cube_name
        # keep_by_attr => no source_dim_mapping
        assert first_kwargs.get("source_dim_mapping") is None

        mdx1 = first_kwargs["data_mdx"]
        # Version & Year are standard TM1SUBSETALL
        assert "TM1SUBSETALL([Version])" in mdx1
        assert "TM1SUBSETALL([Year])" in mdx1

        # Region uses FILTER with attribute logic
        assert "FILTER(" in mdx1
        assert "TM1SUBSETALL([Region])" in mdx1
        assert '[Region].CURRENTMEMBER.PROPERTIES("KeepOnDrop")' in mdx1
        assert '= "Y"' in mdx1

        delete_cube_mock.assert_called_once()
        create_cube_mock.assert_called_once()
        tm1_service.cubes.delete.assert_called_with(temp_cube_name)


    @pytest.mark.parametrize("bad_cfg", [
        {"strategy": "keep_by_attr", "attr_name": "KeepOnDrop"},  # no attr_value
        {"strategy": "keep_by_attr", "attr_value": "Y"},  # no attr_name
        {"strategy": "keep_by_attr"},  # both missing
    ])
    @pytest.mark.skip
    def test_delete_dimensions_keep_by_attr_requires_attr_name_and_value(self, mocker, bad_cfg):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")
        dims_old = ["Region"]
        dims_new = []  # delete Region

        strategies = {"Region": bad_cfg}

        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")

        with pytest.raises(ValueError) as excinfo:
            cube._delete_dimensions_from_cube(
                tm1_service=tm1_service,
                cube_old=cube_old,
                cube_new=cube_new,
                dims_old=dims_old,
                dims_new=dims_new,
                strategies=strategies,
            )

        assert "requires 'attr_name' and 'attr_value'" in str(excinfo.value)
        copy_mock.assert_not_called()


    @pytest.mark.skip
    def test_delete_dimensions_no_deleted_dims_returns_early(self, mocker):
        tm1_service = mocker.Mock()

        cube_old = make_cube("Sales")
        cube_new = make_cube("Sales")

        dims_old = ["Version", "Year"]
        dims_new = ["Version", "Year"]

        cube_cls = mocker.patch("tm1_git_py.model.cube.TM1py.Cube")
        copy_mock = mocker.patch("tm1_git_py.model.cube.data_copy_intercube")
        delete_cube_mock = mocker.patch("tm1_git_py.model.cube.delete_cube")
        create_cube_mock = mocker.patch("tm1_git_py.model.cube.create_cube")

        cube._delete_dimensions_from_cube(
            tm1_service=tm1_service,
            cube_old=cube_old,
            cube_new=cube_new,
            dims_old=dims_old,
            dims_new=dims_new,
        )

        cube_cls.assert_not_called()
        copy_mock.assert_not_called()
        delete_cube_mock.assert_not_called()
        create_cube_mock.assert_not_called()
        tm1_service.cubes.exists.assert_not_called()



class TestProcessCRUD:

    def test_create_process_builds_tm1py_process_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        process_mock = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
        )

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_A",
            has_security_access=True,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )

        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"


    def test_delete_process_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.processes.delete.return_value = "delete-result"
        proc = make_process(name="Proc_To_Delete")

        result = process.delete_process(tm1_service, proc)

        tm1_service.processes.delete.assert_called_once_with("Proc_To_Delete")
        assert result == "delete-result"


    def test_update_process_updates_core_fields_without_param_var_changes(self, mocker):
        tm1_service = mocker.Mock()

        process_new = make_process(
            name="Proc_A",
            has_security_access=True,
            datasource_type="ODBC"
        )

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections coming from the live TM1 object.
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        # Act
        result = process.update_process(tm1_service, process_new)

        tm1_service.processes.get.assert_called_once_with(name_process="Proc_A")

        # Core fields updated
        assert tm1_process_obj.datasource_type == "ODBC"
        assert tm1_process_obj.has_security_access is True

        # No parameter/variable modifications because lists are identical
        tm1_process_obj.add_parameter.assert_not_called()
        tm1_process_obj.remove_parameter.assert_not_called()
        tm1_process_obj.add_variable.assert_not_called()
        tm1_process_obj.remove_variable.assert_not_called()

        # Update call + propagated result
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"


    def test_update_process_adds_and_removes_parameters_and_variables(self, mocker):
        tm1_service = mocker.Mock()

        params_new = [
            {"Name": "p1", "Prompt": "P1", "Value": "1", "Type": "Numeric"},
            {"Name": "p3", "Prompt": "P3", "Value": "3", "Type": "String"},
        ]
        vars_new = [
            {"Name": "v1", "Type": "String"},
            {"Name": "v3", "Type": "String"},
        ]

        process_new = make_process(
            name="Proc_B",
            has_security_access=False,
            datasource_type="None",
            parameters=params_new,
            variables=vars_new,
        )

        params_old = [
            {"Name": "p1", "Prompt": "P1", "Value": "1", "Type": "Numeric"},
            {"Name": "p2", "Prompt": "P2", "Value": "2", "Type": "String"},
        ]
        vars_old = [
            {"Name": "v1", "Type": "String"},
            {"Name": "v2", "Type": "String"},
        ]

        tm1_process_obj = mocker.Mock()
        # Mock the parameter / variable collections fetched from the live TM1 process.
        tm1_process_obj.parameters = list(params_old)
        tm1_process_obj.variables = list(vars_old)
        tm1_service.processes.get.return_value = tm1_process_obj

        update_result = mocker.sentinel.update_result
        tm1_service.processes.update.return_value = update_result

        # Act
        result = process.update_process(tm1_service, process_new)

        # Check add/remove for parameters
        tm1_process_obj.add_parameter.assert_called_once_with(
            name="p3",
            prompt="P3",
            value="3",
            parameter_type="String",
        )
        tm1_process_obj.remove_parameter.assert_called_once_with(name="p2")

        # Check add/remove for variables
        tm1_process_obj.add_variable.assert_called_once_with(
            name="v3",
            variable_type="String",
        )
        tm1_process_obj.remove_variable.assert_called_once_with(name="v2")

        # Ensure update was still called with the process object
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result is update_result

    def test_create_process_accepts_datasource_dict(self, mocker):
        tm1_service = mocker.Mock()
        process_mock = make_process(name="Proc_DictDS", datasource_type={"type": "None"})

        tm1py_process_cls = mocker.patch("tm1_git_py.model.process.TM1py.Process")
        tm1py_process_instance = tm1py_process_cls.return_value
        tm1_service.processes.create.return_value = "create-result"

        result = process.create_process(tm1_service, process_mock)

        tm1py_process_cls.assert_called_once_with(
            name="Proc_DictDS",
            has_security_access=process_mock.hasSecurityAccess,
            datasource_type="None",
            parameters=process_mock.parameters,
            variables=process_mock.variables,
        )
        tm1_service.processes.create.assert_called_once_with(tm1py_process_instance)
        assert result == "create-result"

    def test_update_process_normalizes_empty_datasource_to_none(self, mocker):
        tm1_service = mocker.Mock()
        process_new = make_process(name="Proc_EmptyDS", datasource_type="")

        tm1_process_obj = mocker.Mock()
        tm1_process_obj.parameters = list(process_new.parameters)
        tm1_process_obj.variables = list(process_new.variables)
        tm1_service.processes.get.return_value = tm1_process_obj
        tm1_service.processes.update.return_value = "update-result"

        result = process.update_process(tm1_service, process_new)

        assert tm1_process_obj.datasource_type == "None"
        tm1_service.processes.update.assert_called_once_with(tm1_process_obj)
        assert result == "update-result"



class TestChoreCRUD:

    def test_create_chore_builds_tm1py_chore_and_calls_create(self, mocker):
        tm1_service = mocker.Mock()

        chore_mock = make_chore(
            name="Chore_A",
            start_time="2025-04-22T10:07:00+01:00",
            dst_sensitive=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="P01DT00H00M00S",
            task_names=["Proc1", "Proc2"],
        )

        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0"),
            mocker.Mock(name="ChoreTask1"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )

        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value
        tm1_service.chores.create.return_value = "create-result"

        result = chore.create_chore(tm1_service, chore_mock)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_mock.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_mock.start_time)
        frequency_from_string.assert_called_once_with(chore_mock.frequency)

        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=True,
            active=False,
            execution_mode="SingleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )

        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_delete_chore_calls_tm1_and_returns_response(self, mocker):
        tm1_service = mocker.Mock()
        tm1_service.chores.delete.return_value = "delete-result"
        chore_obj = make_chore(name="Chore_To_Delete")

        result = chore.delete_chore(tm1_service, chore_obj)

        tm1_service.chores.delete.assert_called_once_with("Chore_To_Delete")
        assert result == "delete-result"


    def test_update_chore_updates_fields_and_tasks_when_exists(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_A",
            start_time="2025-04-23T10:00:00+01:00",
            dst_sensitive=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="P02DT00H00M00S",
            task_names=["Proc1_new", "Proc2_new"],
        )

        tm1_service.chores.create.return_value = "create-result"
        create_chore_task_mock = mocker.patch(
            "tm1_git_py.model.chore.create_chore_task"
        )
        chore_task_instances = [
            mocker.Mock(name="ChoreTask0_new"),
            mocker.Mock(name="ChoreTask1_new"),
        ]
        create_chore_task_mock.side_effect = chore_task_instances

        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time",
        )
        frequency_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency",
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        assert create_chore_task_mock.call_count == 2
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[0], step=0)
        create_chore_task_mock.assert_any_call(task=chore_new.tasks[1], step=1)

        start_time_from_string.assert_called_once_with(chore_new.start_time)
        frequency_from_string.assert_called_once_with(chore_new.frequency)
        tm1_service.chores.delete.assert_called_once_with("Chore_A")
        tm1py_chore_cls.assert_called_once_with(
            name="Chore_A",
            start_time="parsed-start-time",
            dst_sensitivity=False,
            active=True,
            execution_mode="MultipleCommit",
            frequency="parsed-frequency",
            tasks=chore_task_instances,
        )
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"


    def test_update_chore_activates_when_active_flag_changes_from_false_to_true(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_B",
            active=True,
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        mocker.patch("tm1_git_py.model.chore.ChoreStartTime.from_string", return_value="parsed-start-time")
        mocker.patch("tm1_git_py.model.chore.ChoreFrequency.from_string", return_value="parsed-frequency")
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        tm1_service.chores.delete.assert_called_once_with("Chore_B")
        tm1py_chore_cls.assert_called_once()
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"

    def test_update_chore_accepts_date_only_start_time(self, mocker):
        tm1_service = mocker.Mock()

        chore_new = make_chore(
            name="Chore_DateOnly",
            start_time="2026-03-05",
            task_names=["ProcX"],
        )

        tm1_service.chores.create.return_value = "create-result"

        mocker.patch("tm1_git_py.model.chore.create_chore_task", return_value=mocker.Mock())
        start_time_from_string = mocker.patch(
            "tm1_git_py.model.chore.ChoreStartTime.from_string",
            return_value="parsed-start-time"
        )
        mocker.patch(
            "tm1_git_py.model.chore.ChoreFrequency.from_string",
            return_value="parsed-frequency"
        )
        tm1py_chore_cls = mocker.patch("tm1_git_py.model.chore.TM1py.Chore")
        tm1py_chore_instance = tm1py_chore_cls.return_value

        result = chore.update_chore(tm1_service, chore_new)

        start_time_from_string.assert_called_once_with("2026-03-05T00:00:00+00:00")
        tm1_service.chores.delete.assert_called_once_with("Chore_DateOnly")
        tm1_service.chores.create.assert_called_once_with(tm1py_chore_instance)
        assert result == "create-result"

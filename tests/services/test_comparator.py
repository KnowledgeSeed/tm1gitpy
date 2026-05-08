from tests.unit_common import *


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
        sig_w1 = seq.recalculate_content_signature_parallel()
        seq.replace_with_payloads(())
        seq.append(Edge("P", "C", 2))
        sig_w2 = seq.recalculate_content_signature_parallel()
        assert sig_w1 != sig_w2


    def test_comparator_no_changes_round_trip(self, tmp_path):
        model1, error1 = deserialize_model(str(test_model_dir_base))
        serialize_model(model=model1, dir=str(tmp_path), max_workers=1)
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

import threading

from tm1_git_py.db.sqlite_worker import SqliteWorker
from tm1_git_py.db.changeset_store import ChangesetStore
from tests.unit_common import *


class TestChangeset:
    def test_sqlite_worker_await_query_result_times_out(self):
        w = object.__new__(SqliteWorker)
        w._file_name = "dummy.sqlite"
        w._result_timeout_seconds = 0.01
        w._lock = threading.Lock()
        w._select_events = {}
        w._results = {}

        with pytest.raises(TimeoutError, match="Timed out waiting"):
            w.fetch_or_wait_for_result("missing-token")

    def test_changeset_store_for_changeset_id_initializes_outside_instances_lock(self, monkeypatch, tmp_path):
        lock = threading.Lock()
        initialized_without_lock = False

        def fake_init(self, *, changeset_id, base_dir=None, require_exists=False):
            nonlocal initialized_without_lock
            initialized_without_lock = lock.acquire(blocking=False)
            if initialized_without_lock:
                lock.release()
            self.db_path = ChangesetStore.path_for(changeset_id=changeset_id, base_dir=base_dir)
            self._closed = False

        monkeypatch.setattr(ChangesetStore, "_instances", {})
        monkeypatch.setattr(ChangesetStore, "_instances_lock", lock)
        monkeypatch.setattr(ChangesetStore, "__init__", fake_init)

        store = ChangesetStore.for_changeset_id(
            changeset_id="lock-check",
            base_dir=str(tmp_path),
        )

        assert store is ChangesetStore._instances[os.path.abspath(store.db_path)]
        assert not initialized_without_lock

    def test_changeset_get_store_concurrent_first_open(self, tmp_path):
        """Multiple threads opening the same new Changeset must not race first-open clear."""
        cs = Changeset(changeset_id="concurrent-store-open", base_dir=str(tmp_path))
        errors: list[BaseException] = []

        def touch_store() -> None:
            try:
                _ = cs._active_store()
            except BaseException as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=touch_store) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive(), "thread hung in _get_store"
        assert not errors
        assert len(cs.changes) == 0

    def test_create_object_ignores_duplicate_create_for_technical_object(self, mocker, caplog):
        process = make_process(name="}Stats")
        duplicate_error = RuntimeError(
            "Text: "
            "'{\"error\":{\"code\":\"278\",\"message\":\"A process with Name \\\"}Stats\\\" already exists.\"}}' "
            "- Status Code: 400 - Reason: 'Bad Request'"
        )
        duplicate_error.status_code = 400
        mock_create = mocker.patch("tm1_git_py.model.process.create_process", side_effect=duplicate_error)

        with caplog.at_level(logging.WARNING):
            response = create_object(
                tm1_service=mocker.Mock(),
                object_instance=process,
                object_type=ObjectType.PROCESS.value,
                uri=process.uri(),
            )

        assert response.ok
        assert response.status_code == 208
        assert "Ignoring duplicate create failure for technical object" in caplog.text
        assert mock_create.call_count == 1

    def test_create_object_raises_duplicate_create_for_non_technical_object(self, mocker):
        process = make_process(name="RegularProcess")
        duplicate_error = RuntimeError(
            "Text: "
            "'{\"error\":{\"code\":\"278\",\"message\":\"A process with Name \\\"RegularProcess\\\" already exists.\"}}' "
            "- Status Code: 400 - Reason: 'Bad Request'"
        )
        duplicate_error.status_code = 400
        mocker.patch("tm1_git_py.model.process.create_process", side_effect=duplicate_error)

        with pytest.raises(RuntimeError, match="already exists"):
            create_object(
                tm1_service=mocker.Mock(),
                object_instance=process,
                object_type=ObjectType.PROCESS.value,
                uri=process.uri(),
            )

    def test_apply_continues_after_duplicate_create_for_technical_object(self, mocker, caplog):
        technical_process = make_process(name="}Stats")
        regular_process = make_process(name="RegularProcess")
        changeset = Changeset(changeset_id="20260420000001")
        changeset.changes = [
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=technical_process.uri(),
                body=technical_process,
                apply=True,
            ),
            Change(
                change_type=ChangeType.ADD,
                object_type=ObjectType.PROCESS,
                uri=regular_process.uri(),
                body=regular_process,
                apply=True,
            ),
        ]

        def create_process_side_effect(_tm1_service, process, **_kwargs):
            if process.name == "}Stats":
                duplicate_error = RuntimeError(
                    "Text: "
                    "'{\"error\":{\"code\":\"278\",\"message\":\"A process with Name \\\"}Stats\\\" already exists.\"}}' "
                    "- Status Code: 400 - Reason: 'Bad Request'"
                )
                duplicate_error.status_code = 400
                raise duplicate_error
            return types.SimpleNamespace(url=process.uri(), status_code=201, ok=True)

        mocker.patch("tm1_git_py.model.process.create_process", side_effect=create_process_side_effect)
        mocker.patch("tm1_git_py.services.apply.delete_object")
        mocker.patch("tm1_git_py.services.apply.update_object")

        with caplog.at_level(logging.WARNING):
            success, changes = apply(tm1_service=mocker.Mock(), changeset=changeset, fail_fast=True)

        assert success
        assert changes == [regular_process.uri(), technical_process.uri()]
        assert "Ignoring duplicate create failure for technical object" in caplog.text

    def test_apply_uses_sorted_order_for_delete(self, mocker):
        model_old, errors_old = deserialize_model(str(test_model_dir_base))
        model_new, errors_new = deserialize_model(str(test_model_dir_diff))
        comparator = Comparator()

        changeset = comparator.compare(model_old, model_new)

        # Patch deletes so we can inspect call order
        mock_delete = mocker.patch("tm1_git_py.services.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.services.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.services.apply.update_object")

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
        mock_delete = mocker.patch("tm1_git_py.services.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.services.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.services.apply.update_object")

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
        mock_delete = mocker.patch("tm1_git_py.services.apply.delete_object")
        mock_create = mocker.patch("tm1_git_py.services.apply.create_object")
        mock_update = mocker.patch("tm1_git_py.services.apply.update_object")

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

        mock_create = mocker.patch("tm1_git_py.services.apply.create_object")
        mocker.patch("tm1_git_py.services.apply.delete_object")
        mocker.patch("tm1_git_py.services.apply.update_object")
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
        changeset._store.clear()
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
        changeset._store.clear()
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
        assert imported._changeset_id == "20260413000008"
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

        assert imported._changeset_id == "20260413000005"
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

    @pytest.mark.skip(reason="Legacy name format is no longer supported")
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

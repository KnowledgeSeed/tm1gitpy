from tests.unit_common import *
import pickle
import threading


class TestSerializer:

    def test_serialize_model_uses_serial_mode_when_resolved_cpu_worker_is_one(self, tmp_path, monkeypatch):
        import tm1_git_py.services.serializer as serializer_module

        seen = {}

        class FakeProcessPoolExecutor:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("process pool should not be created")

        monkeypatch.setattr(serializer_module, "ProcessPoolExecutor", FakeProcessPoolExecutor)
        monkeypatch.setattr(
            serializer_module,
            "serialize_dimensions",
            lambda *_args, **kwargs: seen.setdefault("dimension_process_pool", kwargs.get("process_pool")),
        )
        monkeypatch.setattr(
            serializer_module,
            "serialize_cubes",
            lambda *_args, **kwargs: seen.setdefault("cube_workers", kwargs.get("max_workers")),
        )
        monkeypatch.setattr(
            serializer_module,
            "serialize_processes",
            lambda *_args, **kwargs: seen.setdefault("process_workers", kwargs.get("max_workers")),
        )
        monkeypatch.setattr(
            serializer_module,
            "serialize_chores",
            lambda *_args, **kwargs: seen.setdefault("chore_workers", kwargs.get("max_workers")),
        )

        model = Model(
            cubes=[],
            dimensions=[Dimension(name="D", hierarchies=[], defaultHierarchy=None)],
            processes=[],
            chores=[],
        )

        serialize_model(model, str(tmp_path), max_workers=1)

        assert seen == {
            "dimension_process_pool": None,
        }

    def test_serializer_round_trip_sanity_check(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path), max_workers=1)
        model_deserialized, errors = deserialize_model(str(tmp_path))
        assert model.to_dict() == model_deserialized.to_dict()

    def test_serialize_dimensions_updates_store_backed_source_json_mtime(self, tmp_path):
        import uuid

        model_id = f"serialize_mtime_{uuid.uuid4().hex}"
        hierarchy_obj = Hierarchy(
            name="MyHier",
            dimension_name="MyDim",
            model_id=model_id,
        )
        hierarchy_obj.elements.extend([Element(name="E1", type="Numeric")])
        hierarchy_obj.edges.extend([Edge(parent="E1", component_name="E2", weight=1)])
        hierarchy_obj.subsets.extend([Subset(name="S1", expression="{[MyDim].[MyHier].Members}")])
        dimension_obj = Dimension(
            name="MyDim",
            hierarchies=[hierarchy_obj],
            defaultHierarchy=hierarchy_obj,
        )

        dim_dir = tmp_path / "dimensions"
        dim_dir.mkdir()
        serialize_dimensions(
            [dimension_obj],
            str(dim_dir),
            process_pool=None,
            progress_sink=NoopProgressSink(),
        )

        hierarchy_path = dim_dir / "MyDim.hierarchies" / "MyHier.json"
        expected_mtime_ns = int(hierarchy_path.stat().st_mtime_ns)
        assert hierarchy_obj.elements.source_json_mtime_ns() == expected_mtime_ns
        assert hierarchy_obj.edges.source_json_mtime_ns() == expected_mtime_ns
        assert hierarchy_obj.subsets.source_json_mtime_ns() == expected_mtime_ns

        
    def test_serialize_dimensions_creates_hierarchy_and_subset_files(self, tmp_path):
        model = build_mock_model()
        serialize_model(model, str(tmp_path), max_workers=1)

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
        serialize_model(model, str(tmp_path), max_workers=1)

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
        serialize_model(model, str(tmp_path), max_workers=1)

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
        serialize_model(model, str(tmp_path), max_workers=1)

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

    def test_serialize_cubes_creates_drillthrough_rule_link_and_file(self, tmp_path):
        cube = Cube(
            name="Sales",
            dimensions=["Versions"],
            rules=[Rule(area="[default]", full_statement="[] = N: 1;", name="default")],
            views=[],
            drillthrough_rules=[
                Rule(
                    area="[default]",
                    full_statement="[]=s:'simple_drillthrough';",
                    name="default",
                )
            ],
        )
        model = Model(cubes=[cube], dimensions=[], processes=[], chores=[])

        serialize_model(model, str(tmp_path), max_workers=1)

        cube_dir = tmp_path / "cubes"
        cube_json = json.loads((cube_dir / "Sales.json").read_text(encoding="utf-8"))
        assert cube_json["Rules@Code.link"] == "Sales.rules"
        assert cube_json["DrillthroughRules@Code.link"] == "Sales.drillthrough.rules"
        assert (cube_dir / "Sales.rules").read_text(encoding="utf-8") == "[] = N: 1;"
        assert (
            cube_dir / "Sales.drillthrough.rules"
        ).read_text(encoding="utf-8") == "[]=s:'simple_drillthrough';"

    def test_serialize_cubes_process_pool_ignores_unpicklable_cube_state(self, tmp_path):
        import tm1_git_py.services.serializer as serializer_module

        class LockBackedView:
            def __init__(self, name: str, mdx: str):
                self.name = name
                self.type = "MDXView"
                self.mdx = mdx

            def as_json(self) -> str:
                return json.dumps({"Name": self.name})

        class LockBackedCube:
            def __init__(self):
                self.name = "LockCube"
                self.rules = [object()]
                self.views = [LockBackedView(name="Default", mdx="SELECT 1 ON 0 FROM [LockCube]")]
                self._lock = threading.Lock()

            def get_rule_text(self) -> str:
                return "# rule body"

            def as_json(self) -> str:
                return json.dumps({"Name": self.name})

        cubes_dir = tmp_path / "cubes"
        cubes_dir.mkdir()
        cube = LockBackedCube()
        cube_job = serializer_module._build_cube_serialize_job(cube)
        pickle.dumps((serializer_module._serialize_cube, (cube_job, str(cubes_dir), serializer_module.NoopProgressSink())))

        serializer_module.serialize_cubes(
            [cube],
            str(cubes_dir),
            process_pool=None,
            progress_sink=serializer_module.NoopProgressSink(),
        )

        assert (cubes_dir / "LockCube.json").exists()
        assert (cubes_dir / "LockCube.rules").exists()
        assert (cubes_dir / "LockCube.views" / "Default.json").exists()
        assert (cubes_dir / "LockCube.views" / "Default.mdx").exists()


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
            dimensions=[dimension.name],
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

        serialize_model(special_model, str(tmp_path), max_workers=1)

        dim_path = tmp_path / "dimensions" / f"{special_dim_name}.json"
        cube_path = tmp_path / "cubes" / f"{special_cube_name}.json"
        view_json_path = tmp_path / "cubes" / f"{special_cube_name}.views" / f"{special_view_name}.json"
        process_json_path = tmp_path / "processes" / f"{special_process_name}.json"

        for path in [dim_path, cube_path, view_json_path, process_json_path]:
            assert path.exists(), f"Serialized file missing: {path}"

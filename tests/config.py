import tm1_git_py
from tm1_git_py.model import Process, TI, Cube, MDXView, Dimension, Element, Hierarchy, Edge, Subset, Model, Rule

dim_data = ["""
        {
            "@type": "Dimension",
            "Name": "testbenchPeriod",
            "Hierarchies@Code.links": [
                "testbenchPeriod.hierarchies/<tm1_git_py.model.hierarchy.Hierarchy object at 0x771a5bafcec0>.json"
            ]
        }
    """,
    """
        {
            "@type": "Dimension",
            "Name": "testbenchMeasureSales",
            "Hierarchies@Code.links": [
                "testbenchMeasureSales.hierarchies/<model.hierarchy.Hierarchy object at 0x771a5b7b5490>.json"
            ],
            "DefaultHierarchy": "Dimensions('testbenchMeasureSales')/Hierarchies('testbenchMeasureSales')"
        }
    """,
    """
        {
            "@type": "Dimension",
            "Name": "testbenchMeasureSales",
            "DefaultHierarchy": "Dimensions('testbenchMeasureSales')/Hierarchies('testbenchMeasureSales')"
        }
    """
]

chore_data = [
    """
        {
            "@type": "Chore",
            "Name": "Mock Nightly Maintenance",
            "StartTime": "2024-01-01T01:30:00+00:00",
            "DSTSensitive": true,
            "Active": true,
            "ExecutionMode": "MultipleCommit",
            "Frequency": "P00DT24H00M00S",
            "Tasks": {
                    "Process@odata.bind": "Processes('Mock Process Load Product Data')",
                    "Parameters": []
            }
        }
    """,
     """
        {
            "@type": "Chore",
            "Name": "Mock Weekly Export",
            "StartTime": "2024-01-07T05:00:00+00:00",
            "DSTSensitive": false,
            "Active": true,
            "ExecutionMode": "SingleCommit",
            "Tasks": []
        }
     """
]

process_data = [
    """
        {
            "@type": "Process",
            "Name": "Mock Process Cleanup Subsets",
            "HasSecurityAccess": true,
            "Code@Code.link": "Mock Process Cleanup Subsets.ti",
            "DataSource": {
                "Type": "None"
            },
            "Parameters": [
                    "Name": "pDimension",
                    "Prompt": "Dimension to scan",
                    "Value": "testbenchCustomer",
                    "Type": "String"
            ],
            "Variables": []
        }
    """,
    """
        {
            "@type": "Process",
            "Name": "Mock Process Cleanup Subsets",
            "HasSecurityAccess": true,
            "Code@Code.link": "Mock Process Cleanup Subsets.ti",
            "DataSource": {
                "Type": "None"
            },
            "Parameters": [],
            "Variables": 
                {
                    "Name": "vSubsetName",
                    "Type": "String",
                    "Position": 1,
                    "StartByte": 0,
                    "EndByte": 0
                }
        }
    """
]

def _base_mock_dimension():
    element = Element({"Name": "Leaf1", "Type": "Numeric"})
    hierarchy = Hierarchy(
        name="MockHier",
        elements=[element],
        edges=[],
        subsets=[],
        source_path="dimensions/MockDim.hierarchies/MockHier.json"
    )
    return Dimension(
        name="MockDim",
        hierarchies=[hierarchy],
        defaultHierarchy=hierarchy,
        source_path="dimensions/MockDim.json"
    )


def build_mock_model(include_chore: bool = False, include_rules: bool = False, additional_views: bool = False):
    dimension = _base_mock_dimension()

    view = MDXView(
        name="Default",
        mdx="SELECT {TM1SUBSETALL([MockDim].[MockHier])} ON 0 FROM [MockCube]",
        source_path="cubes/MockCube.views/Default.json"
    )
    views = [view]
    if additional_views:
        views.append(
            MDXView(
                name="AdditionalView",
                mdx="SELECT {TM1FILTERBYLEVEL({TM1SUBSETALL([MockDim].[MockHier])}, 0)} ON 0 FROM [MockCube]",
                source_path="cubes/MockCube.views/AdditionalView.json"
            )
        )
    cube = Cube(
        name="MockCube",
        dimensions=[dimension],
        rules=[Rule(area="[Default]", full_statement="[] = N:1;", comment="")] if include_rules else [],
        views=views,
        source_path="cubes/MockCube.json"
    )

    ti_stub = TI("# prolog", "# metadata", "# data", "# epilog")
    process = Process(
        name="MockProcess",
        hasSecurityAccess=False,
        code_link="MockProcess.ti",
        datasource=None,
        parameters=[],
        variables=[],
        ti=ti_stub,
        source_path="processes/MockProcess.json"
    )

    chores = []
    if include_chore:
        from tm1_git_py.model import Chore
        chore = Chore(
            name="MockChore",
            start_time="2024-01-01T00:00:00+00:00",
            dst_sensitive=False,
            active=True,
            execution_mode="SingleCommit",
            frequency="P01DT00H00M00S",
            tasks=[],
            source_path="chores/MockChore.json"
        )
        chores.append(chore)

    return Model(
        cubes=[cube],
        dimensions=[dimension],
        processes=[process],
        chores=chores
    )


def _build_mock_changeset_data():
    dimension = _base_mock_dimension()
    hierarchy_old = dimension.hierarchies[0]
    hierarchy_new = Hierarchy(
        name=hierarchy_old.name,
        elements=[Element({"Name": "Elem1", "Type": "Numeric"})],
        edges=[],
        subsets=[],
        source_path=hierarchy_old.source_path
    )
    subset_added = Subset(
        name="NewSubset",
        expression="{TM1SUBSETALL([MockDim].[MockHier])}",
        source_path="dimensions/MockDim.hierarchies/MockHier.subsets/NewSubset.json"
    )
    ti_stub = TI("", "", "", "")
    process_added = Process(
        name="MockProcess",
        hasSecurityAccess=False,
        code_link="MockProcess.ti",
        datasource=None,
        parameters=[],
        variables=[],
        ti=ti_stub,
        source_path="processes/MockProcess.json"
    )
    cube_removed = Cube(
        name="MockCube",
        dimensions=[dimension],
        rules=[],
        views=[],
        source_path="cubes/MockCube.json"
    )

    return {
        'dimension_added': dimension,
        'subset_added': subset_added,
        'process_added': process_added,
        'cube_removed': cube_removed,
        'hierarchy_old': hierarchy_old,
        'hierarchy_new': hierarchy_new
    }


def _objects_equal_case_builders():
    def _dimension_case():
        dimension = _base_mock_dimension()
        hierarchy_numeric = dimension.hierarchies[0]
        hierarchy_string = Hierarchy(
            name=hierarchy_numeric.name,
            elements=[Element({"Name": "Leaf1", "Type": "String"})],
            edges=[],
            subsets=[],
            source_path=hierarchy_numeric.source_path
        )
        dim_two = Dimension(
            name=dimension.name,
            hierarchies=[hierarchy_string],
            defaultHierarchy=hierarchy_string,
            source_path=dimension.source_path
        )
        return dimension, dim_two, tm1_git_py.comparator._dimensions_equal_shallow, False

    def _hierarchy_case():
        hierarchy = _base_mock_dimension().hierarchies[0]
        subset = Subset(
            name="SubsetA",
            expression="{SUBSETALL()}",
            source_path="dimensions/MockDim.hierarchies/MockHier.subsets/SubsetA.json"
        )
        hierarchy_one = Hierarchy(
            name=hierarchy.name,
            elements=[Element({"Name": "Leaf", "Type": "Numeric"})],
            edges=[Edge("Parent", "Leaf", 1)],
            subsets=[subset],
            source_path=hierarchy.source_path
        )
        hierarchy_two = Hierarchy(
            name=hierarchy.name,
            elements=[Element({"Name": "Leaf", "Type": "String"})],
            edges=[Edge("Parent", "Leaf", 2)],
            subsets=[subset],
            source_path=hierarchy.source_path
        )
        return hierarchy_one, hierarchy_two, tm1_git_py.comparator._hierarchies_equal_shallow, False

    def _cube_case():
        dimension = _base_mock_dimension()
        view_one = MDXView(
            name="Default",
            mdx="SELECT {TM1SUBSETALL([MockDim])} ON 0 FROM [MockCube]",
            source_path="cubes/MockCube.views/Default.json"
        )
        view_two = MDXView(
            name="Default",
            mdx="SELECT {TM1FILTERBYLEVEL({TM1SUBSETALL([MockDim])}, 0)} ON 0 FROM [MockCube]",
            source_path="cubes/MockCube.views/Default.json"
        )
        cube_one = Cube(
            name="MockCube",
            dimensions=[dimension],
            rules=[],
            views=[view_one],
            source_path="cubes/MockCube.json"
        )
        cube_two = Cube(
            name="MockCube",
            dimensions=[dimension],
            rules=[],
            views=[view_two],
            source_path="cubes/MockCube.json"
        )
        return cube_one, cube_two, tm1_git_py.comparator._cubes_equal_shallow, False

    def _process_case():
        ti_stub = TI("# prolog", "# meta", "# data", "# epilog")
        params = [{
            "Name": "pParam",
            "Prompt": "",
            "Value": "Value",
            "Type": "String"
        }]
        process_one = Process(
            name="MockProcess",
            hasSecurityAccess=False,
            code_link="MockProcess.ti",
            datasource=None,
            parameters=params,
            variables=[],
            ti=ti_stub,
            source_path="processes/MockProcess.json"
        )
        process_two = Process(
            name="MockProcess",
            hasSecurityAccess=False,
            code_link="MockProcess.ti",
            datasource=None,
            parameters=params,
            variables=[],
            ti=ti_stub,
            source_path="processes/MockProcess.json"
        )
        return process_one, process_two, None, True

    return {
        "dimension_shallow": _dimension_case,
        "hierarchy_shallow": _hierarchy_case,
        "cube_shallow": _cube_case,
        "process_exact": _process_case
    }

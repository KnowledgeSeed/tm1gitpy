import tm1_git_py
from tm1_git_py.model.edge import Edge
from tm1_git_py.model.process import Process
from tm1_git_py.model.dimension import Dimension
from tm1_git_py.model.cube import Cube
from tm1_git_py.model.chore import Chore
from tm1_git_py.model.hierarchy import Hierarchy
from tm1_git_py.model.subset import Subset
from tm1_git_py.model.element import Element
from tm1_git_py.model.mdxview import MDXView
from tm1_git_py.model.model import Model
from tm1_git_py.model.ti import TI
from tm1_git_py.model.task import Task
from tm1_git_py.model.rule import Rule
import os
from typing import List, Dict, Set

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


def make_element(name: str, el_type: str = "Numeric") -> Element:
    return Element({"Name": name, "Type": el_type})


def make_hierarchy(
    dimension_name: str = "Dimension_A",
    hierarchy_name: str = "Hierarchy_A",
    elements=None,
    edges=None,
):
    if elements is None:
        element_names = ["E1", "E2"]
        elements = [make_element(n) for n in element_names]

    if edges is None:
        edges = [Edge(parent="Total", name="E1", weight=1)]

    source_path = Hierarchy.as_link(dimension_name_base=dimension_name, name=hierarchy_name)

    return Hierarchy(
        name=hierarchy_name,
        elements=elements,
        edges=edges,
        subsets=[],
        source_path=source_path,
    )


def make_dimension(name: str, hierarchy_names=None, source_path=None) -> Dimension:
    """
    Build a real Dimension object with real Hierarchy objects.
    """
    if source_path is None:
        source_path = "/dimensions/dummy"
    hierarchies = None
    if hierarchy_names:
        hier_source_path = source_path + ".hierarchies/dummy.json"
        hierarchies = [Hierarchy(name=h_name, elements=[], edges=[], subsets=[], source_path=hier_source_path)
                       for h_name in hierarchy_names]
        default_hierarchy = hierarchies[0]
    else:
        default_hierarchy = make_hierarchy(dimension_name=name, hierarchy_name=name)

    return Dimension(
        name=name,
        hierarchies=hierarchies,
        defaultHierarchy=default_hierarchy,
        source_path=source_path+".json",
    )


def make_subset(
    name: str,
    expression: str,
    dimension_name: str = "Dim_A",
    hierarchy_name: str = "Hier_A",
) -> Subset:
    source_path = Subset.as_link(dimension_name_base=dimension_name,
                                 hierarchy_name_base=hierarchy_name,
                                 name=name)
    return Subset(name=name, expression=expression, source_path=source_path)


def make_chore(
    name: str = "Chore_A",
    start_time: str = "2025-04-22T10:07:00+01:00",
    dst_sensitive: bool = True,
    active: bool = False,
    execution_mode: str = "SingleCommit",
    frequency: str = "P01DT00H00M00S",
    task_names=None,
):
    if task_names is None:
        task_names = ["Proc1", "Proc2"]

    tasks = [Task(process_name=p, parameters=[]) for p in task_names]

    return Chore(
        name=name,
        start_time=start_time,
        dst_sensitive=dst_sensitive,
        active=active,
        execution_mode=execution_mode,
        frequency=frequency,
        tasks=tasks,
        source_path=f"/chores/{name}.json",
    )


def make_process(
    name: str = "Proc_A",
    has_security_access: bool = True,
    datasource_type: str = "None",
    parameters=None,
    variables=None,
) -> Process:
    if parameters is None:
        parameters = [
            {"name": "pYear", "prompt": "Year", "value": "2025", "type": "Numeric"},
        ]
    if variables is None:
        variables = [
            {"name": "vCounter", "type": "String"},
        ]

    return Process(
        name=name,
        hasSecurityAccess=has_security_access,
        code_link=f"{name}.ti",
        datasource=datasource_type,
        parameters=parameters,
        variables=variables,
        ti=None,
        source_path=f"/processes/{name}.json",
    )


def make_mdx_view(
    name: str = "View_A",
    mdx: str = "SELECT FROM [Cube_A]",
    source_path: str = "cubes/Cube_A.views/View_A.json",
) -> MDXView:
    return MDXView(name=name, mdx=mdx, source_path=source_path)


def make_rule(area: str, full_statement: str, comment: str = "") -> Rule:
    """
    Build a real Rule object matching tm1_git_py.model.rule.Rule.
    area:   the rule area string, e.g. "['n']"
    full_statement: the TI rule body, e.g. "['n'] = N: 1;"
    comment: optional comment line, e.g. "// comment"
    """
    return Rule(area=area, full_statement=full_statement, comment=comment)


def make_cube(
    name: str = "Cube_A",
    dimension_names=None,
    rules=None,
    views: list[MDXView] = None
):
    if dimension_names is None:
        dimension_names = ["Dim1", "Dim2"]
    if rules is None:
        rule = make_rule(
            area="['n']",
            full_statement="['n'] = N: 1;",
            comment="// old",
        )
        rules = [rule]

    dimensions = []
    for dim_name in dimension_names:
        dim = make_dimension(dim_name, [])
        dimensions.append(dim)

    return Cube(
        name=name,
        dimensions=dimensions,
        rules=rules,
        views=views,
        source_path=f"/cubes/{name}.json",
    )

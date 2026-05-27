from tm1_git_py.model.subset import Subset


def test_static_subset_as_json_uses_tm1git_compact_colons():
    subset = Subset(
        name="TestDimMultiHierStaticSubset",
        element_ids=[
            "Dimensions('TestDimMultiHier')/Hierarchies('TestDimMultiHier')/Elements('DimElem1')",
            "Dimensions('TestDimMultiHier')/Hierarchies('TestDimMultiHier')/Elements('DimElem2')",
        ],
    )
    text = subset.as_json()
    assert '"@type":"Subset"' in text
    assert '"@type": "Subset"' not in text
    assert '"Elements":\n\t[' in text
    assert '"@id":"Dimensions' in text

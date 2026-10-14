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


def test_static_subset_from_rest_payload_decodes_percent_encoded_element_references():
    encoded = (
        "Dimensions('zSYS%20Maintenance%20Parameter')/"
        "Hierarchies('zSYS%20Maintenance%20Parameter')/Elements('Actual%20Month')"
    )
    decoded = (
        "Dimensions('zSYS Maintenance Parameter')/"
        "Hierarchies('zSYS Maintenance Parameter')/Elements('Actual Month')"
    )
    subset = Subset.from_dict(
        {
            "Name": "Actual Forecast Period Update",
            "Expression": None,
            "Elements": [{"@odata.id": encoded}],
        }
    )

    assert subset.element_ids == [decoded]
    assert f'"@id":"{decoded}"' in subset.as_json()
    assert "%20" not in subset.as_json()


def test_static_subset_from_tm1git_file_payload_keeps_element_reference_verbatim():
    element_id = "Dimensions('Rate %20')/Hierarchies('Rate %20')/Elements('100%25')"
    subset = Subset.from_dict({"Name": "Rates", "Elements": [{"@id": element_id}]})

    assert subset.element_ids == [element_id]

from tm1_git_py.db.model_store import ModelStore
from tm1_git_py.services.serializer import _payload_json_from_row


def test_edge_payload_json_parent_name_before_component_name():
    text = _payload_json_from_row(
        "edges",
        ("DimElemC", "a", 1),
    )
    parent_pos = text.index('"ParentName"')
    component_pos = text.index('"ComponentName"')
    assert parent_pos < component_pos


def test_model_store_edge_payload_json_parent_name_before_component_name(tmp_path):
    store = ModelStore(str(tmp_path / "model.db"))
    text = store._payload_json_from_row_for_type(
        "edges",
        ("DimElemC", "a", "1"),
    )
    parent_pos = text.index('"ParentName"')
    component_pos = text.index('"ComponentName"')
    assert parent_pos < component_pos

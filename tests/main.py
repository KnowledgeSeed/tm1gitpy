import json
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import TM1py
from TM1py import TM1Service

from tests import utility
from tests.utility import make_element, make_hierarchy
from tm1_git_py import apply
from tm1_git_py import export, serialize_model, deserialize_model, Comparator, changeset
from tm1_git_py.model import Element, element, hierarchy, Hierarchy
from tm1_git_py.validation import validate_changeset

USER='admin'
PASSWORD='admin'
URL='http://kseed-win1:5379'

def export_model():
    try:
        tm1 = TM1Service(
            base_url=URL,
            user=USER,
            password=PASSWORD,
            ssl=False
        )

        model, errors = export(tm1_conn=tm1)

        # Try to get model/server name from TM1, fallback to host
        model_name = None
        try:
            info = tm1.get_server_info()
            model_name = info.get('ServerName') or info.get('Name') or info.get('serverName')
        except Exception:
            pass
        if not model_name:
            model_name = urlparse('https://kseed-win1:5898').hostname or 'tm1_model'

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_dir = f"model_test_export/base"

        serialize_model(model, dir=str(export_dir))

        try:
            tm1.logout()
        except Exception:
            pass

        return json.dumps({
            'status': 'ok',
            'export_dir': str(export_dir),
            'model_name': model_name,
            'errors': errors
        })
    except Exception as e:
        return json.dumps({'status': 'error', 'message': str(e)}), 500


def compare_models():
    tm1 = TM1Service(
        base_url=URL,
        user=USER,
        password=PASSWORD,
        ssl=False
    )
    try:

        model2, errors2 = deserialize_model(dir="model_test_export/base")

        # Live model from TM1

        #model2, errors2 = deserialize_model(dir='./model_test_export/test_model_diff')
        model1, errors1 = export(tm1_conn=tm1)

        comparator = Comparator()
        changeset = comparator.compare(model1, model2, mode='full')
        print(changeset)

        def _obj_to_json(obj):
            base = {
                'type': getattr(obj, 'type', obj.__class__.__name__),
                'name': getattr(obj, 'name', None),
                'source_path': getattr(obj, 'source_path', None)
            }
            if hasattr(obj, 'to_dict'):
                try:
                    base['data'] = obj.to_dict()
                except Exception:
                    pass
            return base

        result = {
            'status': 'ok',
            'summary': {
                'added': len(changeset.added),
                'removed': len(changeset.removed),
                'modified': len(changeset.modified)
            },
            'added': [_obj_to_json(o) for o in changeset.added],
            'removed': [_obj_to_json(o) for o in changeset.removed],
            'modified': [
                {
                    'old': _obj_to_json(m.get('old')),
                    'new': _obj_to_json(m.get('new')),
                    'changes': m.get('changes')
                } for m in changeset.modified
            ],
            'errors': {
                'model1': errors1,
                'model2': errors2
            }
        }
        #print(result)
        return changeset
    except Exception as e:
        return json.dumps({'status': 'error', 'message': str(e)}), 500
    finally:
        tm1.logout()


def import_changeset_from_deserialized_model():
    tm1_service = TM1Service(
        base_url=URL,
        user=USER,
        password=PASSWORD,
        ssl=False
    )
    fixtures_root = Path(__file__).resolve().parent
    base_dir = fixtures_root / "model_test_export" / "base"
    changeset_path = fixtures_root / "changeset_test.json"

    model1, errors1 = deserialize_model(dir=str(base_dir))
    model2, errors2 = export(tm1_conn=tm1_service)

    imported = changeset.import_changeset_stateful(model1=model1, model2=model2, changeset_file=str(changeset_path))
    print(imported)
    tm1_service.logout()

    #tm1.dimensions.delete(dimension_name="}Views_testbenchSales")
    #print(tm1.dimensions.exists(dimension_name="}Views_testbenchSales"))

    #model = export_model()
    #print(response_json)

    #changeset = compare_models()
    #print("Exporting changeset...")
    #model.export('changeset_test.json')

    #print("Importing changeset...")
    #import_changeset_from_deserialized_model()


def api_update():
    #model = export_model()
    #changeset = compare_models()

    tm1 = TM1Service(
        base_url=URL,
        user=USER,
        password=PASSWORD,
        ssl=False
    )

    try:
        dimension_name = "testbenchVersion"
        hierarchy_name = "testbenchVersion"

        new_elem = Element(data={"name": "TestVersion", "type": "Numeric"})
        update_elem = Element(data={"name": "Test", "type": "Numeric"})
        url = "/api/v1/$batch"

        batch_operation_1 = element.build_element_delete_request(new_elem, dimension_name=dimension_name, hierarchy_name=hierarchy_name)
        new_elem.type = "Consolidated"
        batch_operation_2 = element.build_element_update_request(update_elem, dimension_name=dimension_name, hierarchy_name=hierarchy_name)

        batch_boundary = f"batch_{uuid.uuid4().hex}"
        changeset_boundary = f"changeset_{uuid.uuid4().hex}"

        content_type, body_text = changeset._compose_batch_payload(
            [batch_operation_1, batch_operation_2], target_url=URL,
            batch_boundary=batch_boundary, changeset_boundary=changeset_boundary)
        #print(content_type)
        print(body_text)
        print("\n")
        # Send the batch request
        rest = getattr(tm1, "_tm1_rest", None)
        session = (
                getattr(rest, "_session", None)
                or getattr(rest, "session", None)
                or getattr(rest, "_s", None)
        )
        #resp = changeset.send_tm1_batch_via_requests(tm1_service=tm1, body_text=body_text)

        #print("response:", resp.text)
        #ok_all = bool(getattr(resp, "ok", False)) or (getattr(resp, "status_code", 500) < 400)
        #print("ok all: ", ok_all)

        #elements_from_tm1 = tm1.hierarchies.get(dimension_name=dimension_name, hierarchy_name=hierarchy_name).elements
        #print(elements_from_tm1)

    finally:
        tm1.logout()
        print("end")


def validation():

    tm1 = TM1Service(
        base_url=URL,
        user=USER,
        password=PASSWORD,
        ssl=False
    )

    try:
        model1, errors1 = deserialize_model(dir="model_test_export/base")
        model2, errors2 = deserialize_model(dir="model_test_export/base")
        error_cube = utility.make_cube(name="testbenchError", dimension_names=["testbenchErrorDim"])
        error_cube.views = []

        model2.cubes.append(error_cube)

        comparator = Comparator()
        changes = comparator.compare(model1=model1, model2=model2)
        print(changes)
        changes.errors["deserialization_errors"] = {
            "model1": errors1,
            "model2": errors2
        }

        validate_changeset(tm1_service=tm1, changeset_object=changes, fail_fast=False)
        print(changes.to_json())

    finally:
        tm1.logout()
        print("end")


def execute_ti(tm1_service, master_ti_code):
    process_name = f"}}git_atomic_{uuid.uuid4().hex}"
    process = TM1py.Process(
        name=process_name,
        prolog_procedure=master_ti_code,
        has_security_access=True
    )

    try:
        # 3. Deploy
        tm1_service.processes.create(process)

        # 4. Execute (The Atomic Moment)
        tm1_service.processes.execute(process_name)
        return True

    except Exception as e:
        print(f"Atomic Batch Failed: {e}")
        # Optional: Retrieve error log from server
        raise e

    finally:
        # 5. Cleanup
        if tm1_service.processes.exists(process_name):
            tm1_service.processes.delete(process_name)


def build_ti():
    tm1_service = TM1Service(
        base_url=URL,
        user=USER,
        password=PASSWORD,
        ssl=False
    )

    try:
        new_elem = make_element(name="testbenchSimulation", el_type="Numeric")
        del_element = make_element(name="testbenchElemDelete", el_type="Numeric")
        source_path = Hierarchy.as_link(dimension_name_base="testbenchNonexistent", name="testbenchNewHier")
        new_hier = Hierarchy(
            name="testbenchNewHier",
            elements=[new_elem],
            edges=[],
            subsets=[],
            source_path=source_path + ".json",
        )

        model1, errors1 = deserialize_model(dir="model_test_export/base")
        #dim = [d for d in model1.dimensions if d.name == "testbenchVersion"][0]
        #hierarchies = dim.hierarchies
        #hier_old = [h for h in hierarchies if h.name == "testbenchVersion"][0]
        #hier_old.elements.append(del_element)

        model2, errors2 = deserialize_model(dir="model_test_export/base_0")
        #dim = [d for d in model2.dimensions if d.name == "testbenchVersion"][0]
        #hierarchies = dim.hierarchies
        #hier = [h for h in hierarchies if h.name == "testbenchVersion"][0]
        #hier.elements.append(new_elem)

        """
        ti_lines = []

        # Header
        ti_lines.append("# **** Atomic Changeset Execution ****")
        ti_lines.append("")
        #ti_lines.append("BatchUpdateStart;")
        #ti_lines.append("")

        snippet = hierarchy.build_hierarchy_elements_update_ti(dimension_name="testbenchVersion", hierarchy_old=hier_old, hierarchy_new=hier)
        ti_lines.append(snippet)
        ti_lines.append("")

        snippet = hierarchy.build_hierarchy_create_ti(hierarchy=new_hier, dimension_name="testbenchNonexistent")
        ti_lines.append(snippet)
        ti_lines.append("")
        #ti_lines.append("BatchUpdateFinish(1);")
        #ti_lines.append("")
        """

        #master_ti_code = "\r\n".join(ti_lines)
        #print(master_ti_code)
        compare = Comparator()
        changes = compare.compare(model1, model2)
        changes.sort()

        print(changes)
        master_ti = apply.build_master_changeset_ti(changes)
        print(master_ti)

        #execute_ti(tm1_service, master_ti_code)

    finally:
        tm1_service.logout()


if __name__ == "__main__":
    #validation()
    build_ti()

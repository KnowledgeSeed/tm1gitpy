import io
import json
import logging
from typing import Any, Dict, TYPE_CHECKING

import TM1py
from TM1py import TM1Service, Process
from requests import Response

from tm1_git_py.model.ti import TI
from tm1_git_py.model.tm1git_json import dump_as_tm1git

if TYPE_CHECKING:
    pass

PROCESS_JSON_SPACED_COLON_KEYS: frozenset[str] = frozenset()
# {
#   "@type":"Process",
# 	"Name":"airflow_test_success",
# 	"HasSecurityAccess":false,
# 	"Code@Code.link":"airflow_test_success.ti",
# 	"DataSource":
# 	{
# 		"Type":"None"
# 	},
# 	"Parameters":[],
# 	"Variables":[]
# }

class Process:
    def __init__(
        self,
        name,
        hasSecurityAccess,
        code_link,
        datasource,
        parameters,
        variables,
        ti,
        variables_ui_data=None,
        ui_data=None,
    ):
        self.type = 'Process'
        self.name = name
        self.hasSecurityAccess = hasSecurityAccess
        self.code_link = code_link
        self.datasource = datasource
        self.parameters = parameters
        self.variables = variables
        self.ti = ti
        self.variables_ui_data = _normalize_variables_ui_data(variables_ui_data)
        self.ui_data = _normalize_ui_data(ui_data)

    def as_json(self):
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
            "HasSecurityAccess": self.hasSecurityAccess,
            "Code@Code.link": self.code_link,
        }
        if self.ui_data:
            payload["UIData"] = self.ui_data
        if self.variables_ui_data:
            payload["VariablesUIData"] = self.variables_ui_data
        payload["DataSource"] = _serialize_datasource(self.datasource)
        payload["Parameters"] = self.parameters
        payload["Variables"] = self.variables
        buf = io.StringIO()
        dump_as_tm1git(payload, buf, spaced_colon_keys=PROCESS_JSON_SPACED_COLON_KEYS)
        return buf.getvalue()
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Process):
            return NotImplemented

        if self.name != other.name:
            return False

        if self.hasSecurityAccess != other.hasSecurityAccess:
            return False

        if self.code_link != other.code_link:
            return False

        if _normalize_datasource_for_compare(self.datasource) != _normalize_datasource_for_compare(other.datasource):
            return False

        if self.ti.ti_as_string() != other.ti.ti_as_string():
            return False

        if self.parameters != other.parameters:
            return False

        if self.variables != other.variables:
            return False

        if _normalize_variables_ui_data(self.variables_ui_data) != _normalize_variables_ui_data(other.variables_ui_data):
            return False
        if _normalize_ui_data(self.ui_data) != _normalize_ui_data(other.ui_data):
            return False

        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            self.hasSecurityAccess,
            json.dumps(_normalize_datasource_for_compare(self.datasource), sort_keys=True),
            json.dumps(self.parameters, sort_keys=True),
            json.dumps(self.variables, sort_keys=True),
            json.dumps(_normalize_ui_data(self.ui_data) or "", sort_keys=True),
            json.dumps(_normalize_variables_ui_data(self.variables_ui_data) or [], sort_keys=True),
            self.ti
        ))
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'has_security_access': self.hasSecurityAccess,
            "code_link": self.code_link,
            'datasource' : _serialize_datasource(self.datasource),
            'parameters': self.parameters,
            'variables': self.variables,
            'ui_data': self.ui_data,
            'variables_ui_data': self.variables_ui_data,
            'ti': self.ti.to_dict()
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Process":

        name = data.get("name") or data.get("Name")
        has_security = data.get("has_security_access")
        if has_security is None:
            has_security = data.get("HasSecurityAccess")

        code_link = data.get("code_link") or data.get("Code@Code.link") or data.get("Code")
        datasource = data.get("datasource") or data.get("DataSource")
        if datasource is None:
            datasource = _normalize_datasource_type(datasource)
        parameters = data.get("parameters") or data.get("Parameters") or []
        variables = data.get("variables") or data.get("Variables") or []
        ui_data = data.get("ui_data")
        if ui_data is None:
            ui_data = data.get("UIData")
        variables_ui_data = data.get("variables_ui_data")
        if variables_ui_data is None:
            variables_ui_data = data.get("VariablesUIData")
        ti_payload = data.get("ti") or {}

        ti_obj = TI.from_dict(ti_payload)

        return cls(
            name=name,
            hasSecurityAccess=bool(has_security) if has_security is not None else False,
            code_link=code_link,
            datasource=datasource,
            parameters=parameters,
            variables=variables,
            ti=ti_obj,
            variables_ui_data=variables_ui_data,
            ui_data=ui_data,
        )

    @staticmethod
    def uri_for(process_name: str) -> str:
        return f"Processes('{process_name}')"

    def uri(self) -> str:
        return self.uri_for(self.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _normalize_datasource_type(datasource: Any) -> str:
    if isinstance(datasource, dict):
        value = datasource.get("Type")
        if value is None:
            value = datasource.get("type")
        return str(value or "None")
    if datasource is None:
        return "None"
    text = str(datasource).strip()
    return text or "None"


def _serialize_datasource(datasource: Any) -> dict[str, Any]:
    if isinstance(datasource, dict):
        payload = dict(datasource)
        if "Type" not in payload and "type" in payload:
            payload["Type"] = payload.pop("type")
        payload.pop("password", None)
        payload.setdefault("Type", "None")
        return payload
    return {"Type": _normalize_datasource_type(datasource)}


_DATASOURCE_TM1PY_KWARG_MAP = {
    "Type": "datasource_type",
    "type": "datasource_type",
    "asciiDecimalSeparator": "datasource_ascii_decimal_separator",
    "asciiDelimiterChar": "datasource_ascii_delimiter_char",
    "asciiDelimiterType": "datasource_ascii_delimiter_type",
    "asciiHeaderRecords": "datasource_ascii_header_records",
    "asciiQuoteCharacter": "datasource_ascii_quote_character",
    "asciiThousandSeparator": "datasource_ascii_thousand_separator",
    "dataSourceNameForClient": "datasource_data_source_name_for_client",
    "dataSourceNameForServer": "datasource_data_source_name_for_server",
    "password": "datasource_password",
    "userName": "datasource_user_name",
    "query": "datasource_query",
    "usesUnicode": "datasource_uses_unicode",
    "view": "datasource_view",
    "subset": "datasource_subset",
    "jsonRootPointer": "datasource_json_root_pointer",
    "jsonVariableMapping": "datasource_json_variable_mapping",
}


def _datasource_tm1py_kwargs(datasource: Any) -> dict[str, Any]:
    if not isinstance(datasource, dict):
        return {"datasource_type": _normalize_datasource_type(datasource)}

    kwargs: dict[str, Any] = {}
    for source_key, value in datasource.items():
        target_key = _DATASOURCE_TM1PY_KWARG_MAP.get(source_key)
        if target_key is None:
            continue
        kwargs[target_key] = value
    kwargs.setdefault("datasource_type", "None")
    return kwargs


def _normalize_datasource_for_compare(datasource: Any) -> dict[str, Any]:
    return _serialize_datasource(datasource)


def _normalize_variables_ui_data(variables_ui_data: Any) -> list[str] | None:
    if variables_ui_data is None:
        return None
    if isinstance(variables_ui_data, str):
        text = variables_ui_data.strip()
        return [text] if text else None
    if isinstance(variables_ui_data, (list, tuple)):
        normalized = [str(item) for item in variables_ui_data if str(item).strip()]
        return normalized or None
    text = str(variables_ui_data).strip()
    return [text] if text else None


def _normalize_ui_data(ui_data: Any) -> str | None:
    if ui_data is None:
        return None
    if isinstance(ui_data, str):
        return ui_data if ui_data.strip() else None
    text = str(ui_data)
    return text if text.strip() else None


def create_process(tm1_service: TM1Service, process: Process) -> Response:
    process_kwargs: dict[str, Any] = {
        "name": process.name,
        "has_security_access": process.hasSecurityAccess,
        "parameters": process.parameters,
        "variables": process.variables,
        **_datasource_tm1py_kwargs(process.datasource),
    }
    if process.ui_data is not None:
        process_kwargs["ui_data"] = process.ui_data
    if process.variables_ui_data is not None:
        process_kwargs["variables_ui_data"] = process.variables_ui_data

    process_object = TM1py.Process(**process_kwargs)

    logger.info(f"Creating Process: {process.name}.")
    response = tm1_service.processes.create(process_object)
    if process.ui_data is not None or process.variables_ui_data is not None:
        tm1_service.processes.update(process_object)
    return response


def update_process(tm1_service: TM1Service, process: Process) -> Response:
    process_object = tm1_service.processes.get(name_process=process.name)
    for key, value in _datasource_tm1py_kwargs(process.datasource).items():
        setattr(process_object, key, value)
    process_object.has_security_access = process.hasSecurityAccess
    if process.ui_data is not None:
        process_object.ui_data = process.ui_data
    if process.ti:
        process_object.prolog_procedure = process.ti.prolog_procedure
        process_object.metadata_procedure = process.ti.metadata_procedure
        process_object.data_procedure = process.ti.data_procedure
        process_object.epilog_procedure = process.ti.epilog_procedure

    _update_process_parameters(process_new=process, process_object=process_object)
    _update_process_variables(process_new=process, process_object=process_object)
    if process.variables_ui_data is not None:
        process_object._variables_ui_data = process.variables_ui_data

    logger.info(f"Updating Process: {process.name}.")

    return tm1_service.processes.update(process_object)


def delete_process(tm1_service: TM1Service, process: Process) -> Response:
    logger.info(f"Deleting Process: {process.name}.")
    return tm1_service.processes.delete(process.name)


def _update_process_variables(process_new: Process, process_object: TM1py.Process):
    variables_old = process_object.variables
    process_new = TM1py.Process(
        name=process_new.name,
        has_security_access=process_new.hasSecurityAccess,
        parameters=process_new.parameters,
        variables=process_new.variables,
        **_datasource_tm1py_kwargs(process_new.datasource),
    )
    variables_new = process_new.variables
    if variables_new != variables_old:
        vars_to_add, vars_to_remove = _diff_lists(variables_old, variables_new)
        for var in vars_to_add:
            process_object.add_variable(
                name=var.get('Name'),
                variable_type=var.get('Type')
            )
        logger.info(f"Added Variables: {vars_to_add} to Process: {process_new.name}.")

        for var in vars_to_remove:
            process_object.remove_variable(name=var.get('Name'))
        logger.info(f"Removed Variables: {vars_to_remove} from Process: {process_new.name}.")


def _update_process_parameters(process_new: Process, process_object: TM1py.Process):
    parameters_old = process_object.parameters
    process_new = TM1py.Process(
        name=process_new.name,
        has_security_access=process_new.hasSecurityAccess,
        parameters=process_new.parameters,
        variables=process_new.variables,
        **_datasource_tm1py_kwargs(process_new.datasource),
    )
    parameters_new = process_new.parameters
    if parameters_new != parameters_old:
        params_to_add, params_to_remove = _diff_lists(parameters_old, parameters_new)
        for param in params_to_add:
            process_object.add_parameter(
                name=param.get('Name'),
                prompt=param.get('Prompt'),
                value=param.get('Value'),
                parameter_type=param.get('Type')
            )
        logger.debug(f"Added Parameters: {params_to_add} to Process: {process_new.name}.")

        for param in params_to_remove:
            process_object.remove_parameter(name=param.get('Name'))
        logger.debug(f"Removed Parameters: {params_to_remove} from Process: {process_new.name}.")


def _diff_lists(old_list, new_list):
    old_tuples = {tuple(d.items()): d for d in old_list}
    new_tuples = {tuple(d.items()): d for d in new_list}

    dicts_to_add = [new_tuples[t] for t in (new_tuples.keys() - old_tuples.keys())]
    dicts_to_remove = [old_tuples[t] for t in (old_tuples.keys() - new_tuples.keys())]

    return dicts_to_add, dicts_to_remove

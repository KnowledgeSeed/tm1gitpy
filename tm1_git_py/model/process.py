import json
import logging
from typing import Any, Dict, TYPE_CHECKING, Optional

import TM1py
from TM1py import TM1Service, Process
from requests import Response

from tm1_git_py.model.ti import TI

# Importáljuk a TI osztályt a típus-ellenőrzéshez (type hinting)
if TYPE_CHECKING:
    pass
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
    def __init__(self, name, hasSecurityAccess, code_link, datasource, parameters, variables, ti, source_path: str):
        self.type = 'Process'
        self.name = name
        self.hasSecurityAccess = hasSecurityAccess
        self.code_link = code_link
        self.datasource = datasource
        self.parameters = parameters
        self.variables = variables
        self.ti = ti
        self.source_path = source_path

    # def __init__(self, name: str, hasSecurityAccess: bool, parameters: List[Dict], variables: List[Dict], data_source: Dict, ti: 'TI', code_link: str):
    #     self.name = name
    #     self.hasSecurityAccess = hasSecurityAccess
    #     self.parameters = parameters
    #     self.variables = variables
    #     self.code_link = code_link

    #     self.data_source_type = data_source.get('Type')
    #     self.data_source_name = data_source.get('Name')

    #     self.ti = ti

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "HasSecurityAccess": self.hasSecurityAccess,
            "Code@Code.link": self.code_link,
            "DataSource": {"Type": "None"},
            "Parameters": self.parameters,
            "Variables": self.variables
        }, indent='\t')
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Process):
            return NotImplemented

        if self.name != other.name:
            return False

        if self.hasSecurityAccess != other.hasSecurityAccess:
            return False

        if self.code_link != other.code_link:
            return False

        other.datasource = other.datasource or None
        self.datasource = self.datasource or None
        if self.datasource != other.datasource:
            return False

        if self.ti.ti_as_string() != other.ti.ti_as_string():
            return False

        if self.parameters != other.parameters:
            return False

        if self.variables != other.variables:
            return False

        return True
        #return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash((
            self.name,
            self.hasSecurityAccess,
            #self.data_source_type,
            #self.data_source_name,
            self.datasource,
            json.dumps(self.parameters, sort_keys=True),
            json.dumps(self.variables, sort_keys=True),
            self.ti
        ))

    def to_dict(self):
        return {
            'name': self.name,
            'has_security_access': self.hasSecurityAccess,
            #'data_source_type': self.data_source_type,
            #'data_source_name': self.data_source_name,
            'datasource' : self.datasource,
            'parameters': self.parameters,
            'variables': self.variables,
            'ti': self.ti.to_dict()
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any],
            *,
            source_path: Optional[str] = None
    ) -> "Process":

        name = data.get("name") or data.get("Name")
        resolved_path = source_path or f"processes/{name}.json"

        has_security = data.get("has_security_access")
        if has_security is None:
            has_security = data.get("HasSecurityAccess")

        code_link = data.get("code_link") or data.get("Code@Code.link") or data.get("Code")
        datasource = data.get("datasource") or data.get("DataSource")
        parameters = data.get("parameters") or data.get("Parameters") or []
        variables = data.get("variables") or data.get("Variables") or []
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
            source_path=resolved_path
        )

    @staticmethod
    def as_link(name : str):
        # /processes/Process_A.json
        return '/processes/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_process(tm1_service: TM1Service, process: Process) -> Response:
    process_object = TM1py.Process(
        name=process.name,
        has_security_access=process.hasSecurityAccess,
        datasource_type=process.datasource,
        parameters=process.parameters,
        variables=process.variables
    )
    logger.info(f"Creating Process: {process.name}.")
    return tm1_service.processes.create(process_object)


def update_process(tm1_service: TM1Service, process: Dict[str, Any]) -> Response:
    process_new = process.get('new')
    process_old = process.get('old')

    if tm1_service.processes.exists(name_process=process_new.name):
        process_object = tm1_service.processes.get(name_process=process_new.name)
        process_object.datasource_type = process_new.datasource
        process_object.has_security_access = process_new.hasSecurityAccess

        _update_process_parameters(process_old=process_old, process_new=process_new, process_object=process_object)
        _update_process_variables(process_old=process_old, process_new=process_new, process_object=process_object)

        logger.info(f"Updating Process: {process_new.name}.")

        return tm1_service.processes.update(process_object)
    else:
        raise ValueError(f"Cannot update Process: '{process_new.name}', Process does not exist")


def delete_process(tm1_service: TM1Service, process_name: str) -> Response:
    logger.info(f"Deleting Process: {process_name}.")
    return tm1_service.processes.delete(process_name)


def _update_process_variables(process_old: Process, process_new: Process, process_object: TM1py.Process):
    if process_new.variables != process_old.variables:
        vars_to_add, vars_to_remove = _diff_lists(process_old.variables, process_new.variables)
        for var in vars_to_add:
            process_object.add_variable(
                name=var.get('name'),
                variable_type=var.get('type')
            )
        logger.info(f"Added Variables: {vars_to_add} to Process: {process_new.name}.")

        for var in vars_to_remove:
            process_object.remove_variable(name=var.get('name'))
        logger.info(f"Removed Variables: {vars_to_remove} from Process: {process_new.name}.")


def _update_process_parameters(process_old: Process, process_new: Process, process_object: TM1py.Process):
    if process_new.parameters != process_old.parameters:
        params_to_add, params_to_remove = _diff_lists(process_old.parameters, process_new.parameters)
        for param in params_to_add:
            process_object.add_parameter(
                name=param.get('name'),
                prompt=param.get('prompt'),
                value=param.get('value'),
                parameter_type=param.get('type')
            )
        logger.debug(f"Added Parameters: {params_to_add} to Process: {process_new.name}.")

        for param in params_to_remove:
            process_object.remove_parameter(name=param.get('name'))
        logger.debug(f"Removed Parameters: {params_to_remove} from Process: {process_new.name}.")


def _diff_lists(old_list, new_list):
    old_tuples = {tuple(d.items()): d for d in old_list}
    new_tuples = {tuple(d.items()): d for d in new_list}

    dicts_to_add = [new_tuples[t] for t in (new_tuples.keys() - old_tuples.keys())]
    dicts_to_remove = [old_tuples[t] for t in (old_tuples.keys() - new_tuples.keys())]

    return dicts_to_add, dicts_to_remove

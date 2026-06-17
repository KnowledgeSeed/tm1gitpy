import json
import logging

import TM1py
from TM1py import TM1Service, Chore, ChoreStartTime, ChoreFrequency
from requests import Response
from typing import Any, Dict, List, Optional, Union

from tm1_git_py.model.task import Task, create_chore_task

# {
# 	"@type": "Chore",
# 	"Name": "ffff",
# 	"StartTime": "2025-04-22T10:07:00+01:00",
# 	"DSTSensitive": true,
# 	"Active": false,
# 	"ExecutionMode": "SingleCommit",
# 	"Frequency": "P01DT00H00M00S",
# 	"Tasks": [
# 		{
# 			"Process@odata.bind": "Processes('zSYS Analogic Load')",
# 			"Parameters": [
# 				{
# 					"Name": "pDimensionFileName",
# 					"Value": "Product.csv"
# 				},
# 				{
# 					"Name": "pElementsFileName",
# 					"Value": "termek.csv"
# 				},
# 				{
# 					"Name": "pEnableDeleteAll",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateBase",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateMovements",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOpening",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOther",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateSubsets",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTDAttributes",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTransactions",
# 					"Value": 0
# 				}
# 			]
# 		},
# 		{
# 			"Process@odata.bind": "Processes('zSYS Analogic Load Channel Dimension Update')",
# 			"Parameters": [
# 				{
# 					"Name": "pVersion",
# 					"Value": "Base"
# 				}
# 			]
# 		},
# 		{
# 			"Process@odata.bind": "Processes('zSYS Analogic Load')",
# 			"Parameters": [
# 				{
# 					"Name": "pDimensionFileName",
# 					"Value": "Product.csv"
# 				},
# 				{
# 					"Name": "pElementsFileName",
# 					"Value": "termek.csv"
# 				},
# 				{
# 					"Name": "pEnableDeleteAll",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateBase",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateMovements",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOpening",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOther",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateSubsets",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTDAttributes",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTransactions",
# 					"Value": 0
# 				}
# 			]
# 		},
# 		{
# 			"Process@odata.bind": "Processes('zSYS Analogic Load')",
# 			"Parameters": [
# 				{
# 					"Name": "pDimensionFileName",
# 					"Value": "Product.csv"
# 				},
# 				{
# 					"Name": "pElementsFileName",
# 					"Value": "termek.csv"
# 				},
# 				{
# 					"Name": "pEnableDeleteAll",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateBase",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateMovements",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOpening",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOther",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateSubsets",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTDAttributes",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTransactions",
# 					"Value": 0
# 				}
# 			]
# 		},
# 		{
# 			"Process@odata.bind": "Processes('zSYS Analogic Load')",
# 			"Parameters": [
# 				{
# 					"Name": "pDimensionFileName",
# 					"Value": "Product.csv"
# 				},
# 				{
# 					"Name": "pElementsFileName",
# 					"Value": "termek.csv"
# 				},
# 				{
# 					"Name": "pEnableDeleteAll",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateBase",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateMovements",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOpening",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateOther",
# 					"Value": 0
# 				},
# 				{
# 					"Name": "pEnableUpdateSubsets",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTDAttributes",
# 					"Value": 1
# 				},
# 				{
# 					"Name": "pEnableUpdateTransactions",
# 					"Value": 0
# 				}
# 			]
# 		}
# 	]
# }

class Chore:
    def __init__(self, name: str, start_time: str, dst_sensitive: bool, active: bool,
                 execution_mode: str, frequency: str, tasks: List[Task]):
        self.type = 'Chore'
        self.name = name
        self.start_time = start_time
        self.dst_sensitive = dst_sensitive
        self.active = active
        self.execution_mode = execution_mode
        self.frequency = frequency
        self.tasks = tasks

    def as_json(self) -> str:
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "StartTime": self.start_time,
            "DSTSensitive": self.dst_sensitive,
            "Active": self.active,
            "ExecutionMode": self.execution_mode,
            "Frequency": self.frequency,
            "Tasks": [task.as_json_dict() for task in self.tasks],
        }, indent='\t')

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Chore):
            return NotImplemented
        return self.name == other.name and \
               self.start_time == other.start_time and \
               self.dst_sensitive == other.dst_sensitive and \
               self.active == other.active and \
               self.execution_mode == other.execution_mode and \
               self.frequency == other.frequency and \
               set(self.tasks) == set(other.tasks)

    def __hash__(self) -> int:
        return hash((self.name, self.start_time, self.dst_sensitive, self.active,
                     self.execution_mode, self.frequency, frozenset(self.tasks)))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'start_time': self.start_time,
            'dst_sensitive': self.dst_sensitive,
            'active': self.active,
            'execution_mode': self.execution_mode,
            'frequency': self.frequency,
            'tasks': [task.as_json_dict() for task in self.tasks]
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Chore":

        name = data.get("name") or data.get("Name")
        start_time = data.get("start_time") or data.get("StartTime")
        dst_sensitive = data.get("dst_sensitive")
        if dst_sensitive is None:
            dst_sensitive = data.get("DSTSensitive")
        active = data.get("active")
        if active is None:
            active = data.get("Active")
        execution_mode = data.get("execution_mode") or data.get("ExecutionMode")
        frequency = data.get("frequency") or data.get("Frequency")
        task_payloads = data.get("tasks") or data.get("Tasks") or []
        tasks = [Task.from_dict(task_payload) for task_payload in task_payloads]

        return cls(
            name=name,
            start_time=start_time,
            dst_sensitive=bool(dst_sensitive) if dst_sensitive is not None else False,
            active=bool(active) if active is not None else False,
            execution_mode=execution_mode,
            frequency=frequency,
            tasks=tasks,
        )

    @staticmethod
    def uri_for(chore_name: str) -> str:
        return f"Chores('{chore_name}')"

    def uri(self) -> str:
        return self.uri_for(self.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def _normalize_start_time(start_time: Union[str, None]) -> str:
    value = (start_time or "").strip()
    if not value:
        # Safe default when source payload is incomplete.
        return "1970-01-01T00:00:00+00:00"
    if "T" in value:
        return value
    # Changeset payloads may provide date-only start_date.
    return f"{value}T00:00:00+00:00"


def _normalize_task_parameters_for_process(tm1_service: TM1Service, task: Task) -> Task:
    """
    TM1 requires chore task parameter count/order to match the referenced process.
    Build a normalized parameter list from live process metadata when available.
    """
    try:
        process_obj = tm1_service.processes.get(task.process_name)
        process_parameters = getattr(process_obj, "parameters", []) or []
        if not process_parameters:
            return Task(process_name=task.process_name, parameters=[])

        provided_by_name: dict[str, Any] = {}
        for payload in task.parameters or []:
            name = payload.get("Name") or payload.get("name")
            if name is not None:
                provided_by_name[str(name)] = payload.get("Value") if "Value" in payload else payload.get("value")

        normalized_parameters: list[dict[str, Any]] = []
        for process_param in process_parameters:
            param_name = process_param.get("Name") or process_param.get("name")
            if param_name is None:
                continue
            default_value = process_param.get("Value") if "Value" in process_param else process_param.get("value")
            normalized_parameters.append({
                "Name": param_name,
                "Value": provided_by_name.get(param_name, default_value)
            })

        return Task(process_name=task.process_name, parameters=normalized_parameters)
    except Exception:
        # If process metadata is unavailable, keep original payload and let TM1 validate.
        return task


def create_chore(tm1_service: TM1Service, chore: Chore) -> Response:
    normalized_tasks = [
        _normalize_task_parameters_for_process(tm1_service=tm1_service, task=chore_task)
        for chore_task in chore.tasks
    ]
    chore_tasks = [create_chore_task(task=chore_task, step=i) for i, chore_task in enumerate(normalized_tasks)]
    frequency = chore.frequency
    start_time = _normalize_start_time(chore.start_time)
    chore_object = TM1py.Chore(
        name=chore.name,
        start_time=ChoreStartTime.from_string(start_time),
        dst_sensitivity=chore.dst_sensitive,
        active=chore.active,
        execution_mode=chore.execution_mode,
        frequency=ChoreFrequency.from_string(frequency),
        tasks=chore_tasks
    )
    task_names = [proc.process_name for proc in normalized_tasks]
    logger.info(f"Creating Chore: {chore.name} with Tasks: {task_names}.")

    return tm1_service.chores.create(chore_object)


def update_chore(tm1_service: TM1Service, chore: Chore) -> Response:
    delete_chore(tm1_service=tm1_service, chore=chore)
    response = create_chore(tm1_service=tm1_service, chore=chore)
    return response


def delete_chore(tm1_service: TM1Service, chore: Chore) -> Response:
    logger.info(f"Deleting Chore: {chore.name}.")
    return tm1_service.chores.delete(chore.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str) -> str:
    return str(value).replace("'", "''") if value else ""


def build_chore_create_ti(chore: Chore) -> str:
    chore_clean = _escape_ti(chore.name)

    lines = []
    lines.append(f"# --- Create Chore: {chore_clean} ---")

    return "\r\n".join(lines)


def build_chore_update_ti(chore: Dict[str, Any]) -> str:
    chore_clean = _escape_ti(chore.get("new").name)
    lines = [f"# --- Update Chore: {chore_clean} ---"]
    return "\r\n".join(lines)


def build_chore_delete_ti(chore: Chore) -> str:
    chore_clean = _escape_ti(chore.name)

    lines = []
    lines.append(f"# --- Delete Chore: {chore_clean} ---")

    return "\r\n".join(lines)

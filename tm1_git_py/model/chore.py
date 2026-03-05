import json
import logging

import TM1py
from TM1py import TM1Service, Chore, ChoreStartTime, ChoreFrequency
from requests import Response
from typing import Any, Dict, List, Optional

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
                 execution_mode: str, frequency: str, tasks: List[Task], source_path: str):
        self.type = 'Chore'
        self.name = name
        self.start_time = start_time
        self.dst_sensitive = dst_sensitive
        self.active = active
        self.execution_mode = execution_mode
        self.frequency = frequency
        self.tasks = tasks
        self.source_path = source_path

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
            data: Dict[str, Any],
            *,
            source_path: Optional[str] = None
    ) -> "Chore":

        name = data.get("name") or data.get("Name")
        resolved_path = source_path or f"chores/{name}.json"

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
            source_path=resolved_path
        )

    @staticmethod
    def as_link(name :str):
        # /chores/chore.json
        return '/chore/' + name


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_chore(tm1_service: TM1Service, chore: Chore) -> Response:
    chore_tasks = [create_chore_task(task=chore_task, step=i) for i, chore_task in enumerate(chore.tasks)]
    frequency = chore.frequency
    start_time = chore.start_time
    chore_object = TM1py.Chore(
        name=chore.name,
        start_time=ChoreStartTime.from_string(start_time),
        dst_sensitivity=chore.dst_sensitive,
        active=chore.active,
        execution_mode=chore.execution_mode,
        frequency=ChoreFrequency.from_string(frequency),
        tasks=chore_tasks
    )
    task_names = [proc.process_name for proc in chore.tasks]
    logger.info(f"Creating Chore: {chore.name} with Tasks: {task_names}.")

    return tm1_service.chores.create(chore_object)


def update_chore(tm1_service: TM1Service, chore: Dict[str, Any]) -> Response:
    chore_new = chore.get('new')

    chore_tasks = [create_chore_task(task=chore_task, step=i) for i, chore_task in enumerate(chore_new.tasks)]

    frequency = chore_new.frequency
    start_time = chore_new.start_time

    chore_object = tm1_service.chores.get(chore_name=chore_new.name)
    chore_object.start_time = ChoreStartTime.from_string(start_time)
    chore_object.dst_sensitivity = chore_new.dst_sensitive
    chore_object.execution_mode = chore_new.execution_mode
    chore_object.frequency = ChoreFrequency.from_string(frequency)
    chore_object.tasks = chore_tasks

    if chore_object.active != chore_new.active:
        if chore_new.active: chore_object.activate()
        if not chore_new.active: chore_object.deactivate()

    task_names = [proc.process_name for proc in chore_new.tasks]
    logger.info(f"Updating Chore: {chore_new.name} with Tasks: {task_names}.")

    return tm1_service.chores.update(chore_object)


def delete_chore(tm1_service: TM1Service, chore_name: str) -> Response:
    logger.info(f"Deleting Chore: {chore_name}.")
    return tm1_service.chores.delete(chore_name)


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

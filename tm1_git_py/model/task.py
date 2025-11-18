import json
from typing import List, Dict, Any, Optional

import TM1py
from TM1py import ChoreTask
from TM1py.Services import TM1Service
from requests import Response

from .process import Process

class Task:
    def __init__(self, process_name: str, parameters: List[Dict[str, Any]]):
        self.process_name = process_name
        self.parameters = parameters
        self.process: Optional[Process] = None

    def link_process(self, processes: List[Process]):
        for p in processes:
            if p.name == self.process_name:
                self.process = p
                break

    def as_json_dict(self) -> Dict[str, Any]:
        return {
            "Process@odata.bind": f"Processes('{self.process_name}')",
            "Parameters": self.parameters
        }

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Task):
            return NotImplemented
        return self.process_name == other.process_name and self.parameters == other.parameters

    def __hash__(self) -> int:
        return hash((self.process_name, json.dumps(self.parameters, sort_keys=True)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            'process_name': self.process_name,
            'parameters': self.parameters,
            'process': self.process.to_dict() if self.process else None
        }


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def create_chore_task(task: Task, step: int) -> ChoreTask:
    return TM1py.ChoreTask(process_name=task.process_name, parameters=task.parameters, step=step)

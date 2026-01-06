import json
import logging
import re
from typing import List, Dict, Any, Optional
import TM1py
from TM1py import ChoreTask

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

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Task":

        def _parse_process_binding(binding: Optional[str]) -> Optional[str]:
            if not binding:
                return None
            match = re.search(r"Processes\('([^']+)'\)", binding)
            if match:
                return match.group(1)
            return binding

        process_ref = payload.get("Process@odata.bind") or payload.get("process_name") or payload.get("process")
        process_name = _parse_process_binding(process_ref)
        parameters = payload.get("Parameters") or payload.get("parameters") or []
        return cls(process_name=process_name or "", parameters=parameters)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

def create_chore_task(task: Task, step: int) -> ChoreTask:
    try:
        logger.info(f"Converting Task: {task.process_name} to ChoreTask object.")
        return TM1py.ChoreTask(process_name=task.process_name, parameters=task.parameters, step=step)
    except Exception:
        raise  ValueError(f"Convertion of Task: {task.process_name} to ChoreTask unsuccessful.")

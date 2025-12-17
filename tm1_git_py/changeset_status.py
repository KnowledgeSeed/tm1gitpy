import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Union

from requests import Response


@dataclass
class ChangeSetOperationLog:
    index: int
    action: str  # CREATE / UPDATE / DELETE
    object_type: str
    object_name: Optional[str] = None
    source_path: Optional[str] = None
    before_state: Optional[dict] = None
    ok: Optional[bool] = None
    status_code: Optional[int] = None
    url: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class ChangeSetExecutionStatus:
    execution_id: str
    changeset_name: Optional[str] = None

    state: str = "PENDING"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: Optional[float] = None

    total_operations: int = 0
    completed_operations: int = 0
    current_operation: Optional[int] = None

    operations: Optional[List[ChangeSetOperationLog]] = None

    failure_action: Optional[str] = None
    failure_object: Optional[str] = None
    failure_reason: Optional[str] = None


class ChangeSetStatusStore:
    """
    File-based execution status store for outside polling.

    Writes JSON to:
        <status_dir>/<execution_id>.json
    """

    def __init__(self, status_dir: Union[str, Path], execution_id: Optional[str] = None, changeset_name: Optional[str] = None):
        self.status_dir = Path(status_dir).expanduser().resolve()
        self.status_dir.mkdir(parents=True, exist_ok=True)

        self.execution_id = execution_id or uuid.uuid4().hex
        self.path = self.status_dir / f"{self.execution_id}.json"

        self.status = ChangeSetExecutionStatus(
            execution_id=self.execution_id,
            changeset_name=changeset_name,
            operations=[],
        )

    def _write(self) -> None:
        self.status.updated_at = time.time()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self.status), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def start(self, total_operations: int) -> str:
        self.status.state = "RUNNING"
        self.status.started_at = time.time()
        self.status.total_operations = total_operations
        self.status.completed_operations = 0
        self.status.current_operation = None
        self._write()
        return self.execution_id

    def begin_operation(
            self,
            index: int,
            action: str,
            object_type: str,
            object_name: Optional[str],
            source_path: Optional[str],
            before_state: Optional[dict] = None
    ) -> None:
        assert self.status.operations is not None
        self.status.operations.append(ChangeSetOperationLog(
            index=index,
            action=action,
            object_type=object_type,
            object_name=object_name,
            source_path=source_path,
            before_state=before_state,
            started_at=time.time(),
        ))
        self.status.current_operation = index
        self._write()

    def end_operation_with_response(self, resp: Response) -> None:
        assert self.status.operations
        op = self.status.operations[-1]

        op.ok = bool(resp.ok)
        op.status_code = int(resp.status_code)
        op.url = getattr(resp, "url", None)
        op.finished_at = time.time()

        if not resp.ok:
            try:
                body = resp.text
            except Exception:
                body = "<unable to read response body>"
            op.error = _truncate(body, 10_000)

            self.status.failure_action = op.action
            self.status.failure_object = f"{op.object_type}:{op.object_name or ''}".strip(":")
            self.status.failure_reason = op.error

        self.status.completed_operations += 1
        self._write()

    def end_operation_with_exception(self, exc: Exception) -> None:
        assert self.status.operations
        op = self.status.operations[-1]

        op.ok = False
        op.error = _truncate(str(exc), 10_000)
        op.finished_at = time.time()

        self.status.failure_action = op.action
        self.status.failure_object = f"{op.object_type}:{op.object_name or ''}".strip(":")
        self.status.failure_reason = op.error

        self.status.completed_operations += 1
        self._write()

    def succeed(self) -> None:
        self.status.state = "SUCCEEDED"
        self.status.finished_at = time.time()
        self.status.current_operation = None
        self._write()

    def fail(self) -> None:
        self.status.state = "FAILED"
        self.status.finished_at = time.time()
        self.status.current_operation = None
        self._write()

    @staticmethod
    def load(status_dir: str | Path, execution_id: str) -> ChangeSetExecutionStatus:
        path = Path(status_dir).expanduser().resolve() / f"{execution_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        ops = [ChangeSetOperationLog(**op) for op in raw.get("operations", [])]
        raw["operations"] = ops
        return ChangeSetExecutionStatus(**raw)


def poll_execution_status(status_dir: str | Path, execution_id: str) -> ChangeSetExecutionStatus:
    return ChangeSetStatusStore.load(status_dir=status_dir, execution_id=execution_id)


def wait_for_completion(status_dir: str | Path, execution_id: str, interval_seconds: float = 2.0, timeout_seconds: float | None = None) -> ChangeSetExecutionStatus:
    start = time.time()
    while True:
        status = poll_execution_status(status_dir, execution_id)
        if status.state in ("SUCCEEDED", "FAILED"):
            return status
        if timeout_seconds is not None and (time.time() - start) > timeout_seconds:
            return status
        time.sleep(interval_seconds)


def _truncate(s: str, limit: int) -> str:
    if not s:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"\n... <truncated, {len(s) - limit} chars omitted>")

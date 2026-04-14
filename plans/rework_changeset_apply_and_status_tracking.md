# Plan: Rework changeset apply and status tracking

## Overview

Rework the apply flow and status tracking so that: (1) `changeset_name` stays part of the changeset with **current `__init__` logic kept**, and is a **non-optional** field (`str`) on the changeset; (2) `execution_id` is generated during apply as `changeset_name + timestamp`; (3) `status_dir` is removed and apply returns the execution status in the response; (4) **`last_execution_id` is removed from Changeset**.

---

## Goals

| Current | Desired |
|--------|--------|
| `changeset_name` passed into `apply()` and/or defaulted in `Changeset.__init__` | Keep current `Changeset.__init__` logic for `changeset_name`. `changeset_name` is a **non-optional** attribute of the changeset (type `str`). |
| Caller can pass `execution_id` into `apply()` | `execution_id` is generated at the start of each apply run as `changeset_name + timestamp`, timestamp format `yyyyMMddHHmmss`. |
| Caller passes `status_dir`; status written to file | No `status_dir`. Apply builds status in memory and returns it; caller may serialize if desired. |
| `Changeset.last_execution_id` | **Remove** `last_execution_id` from Changeset. Callers get execution id from the apply result (status) when needed. |

---

## 1. Changeset: keep changeset_name init, make changeset_name non-optional, remove last_execution_id

**File:** [tm1_git_py/changeset.py](tm1_git_py/changeset.py)

- **Keep** the current `__init__` logic for `changeset_name`: `self.changeset_name: str = changeset_name or str(uuid.uuid4())` (or equivalent). The parameter can remain `Optional[str] = None` for the constructor; after init, `changeset_name` is always a `str`.
- Declare **`changeset_name` as a non-optional attribute** of the Changeset (type `str`). If Changeset is or becomes a dataclass, `changeset_name: str` (no Optional).
- **Remove** `last_execution_id` from Changeset entirely (no attribute, no assignment). Any code that read or wrote `last_execution_id` must be updated (see call sites).
- `Changeset.apply()` will no longer accept `status_dir`, `execution_id`, or `changeset_name`; the apply layer uses `changeset.changeset_name` (always a str) when building status and generating `execution_id`.

---

## 2. Apply: generate execution_id, remove status_dir and status_dir-based I/O

**File:** [tm1_git_py/apply.py](tm1_git_py/apply.py)

- **Signature change:** Remove parameters `status_dir`, `execution_id`, and `changeset_name`. Keep `fail_fast`.
  - New signature: `apply(changeset, tm1_service, *, fail_fast: bool = True) -> ApplyResult` (or a 3-tuple; see below).
- **Execution id:** At the start of apply (when there are changes to apply), generate `execution_id` inside apply as **`<changeset_name><timestamp>`** (concatenated), where timestamp format is **`yyyyMMddHHmmss`** (e.g. `20250227143022`). Example: `my_changeset20250227143022`. Use `changeset.changeset_name` (always a str). Do not accept execution_id from the caller.
- **Status building:** Always build execution status in memory when applying changes (no file I/O inside apply):
  - Create an in-memory status recorder that implements the same lifecycle as today (start, begin_operation, end_operation_with_response / end_operation_with_exception, succeed/fail) but does not write to disk.
  - Use `changeset.changeset_name` when constructing the status.
- **Return value:** Return a result that includes success, the list of change URLs (or None when no changes), and the execution status:
  - **Option A (recommended):** Introduce a small dataclass, e.g. `ApplyResult(success: bool, changes: Optional[list], status: Optional[ChangeSetExecutionStatus])`. When `changeset.has_changes()` is false, return `ApplyResult(success=True, changes=None, status=None)`. When there are changes, `status` is always set (with the generated `execution_id`).
  - **Option B:** Keep a tuple: `(success, changes, status)` with `status` optional when no changes were applied.
- **Backward compatibility:** Existing callers that expect `(success, changes)` will break. Callers must be updated to the new return type and to stop passing `status_dir` / `execution_id` / `changeset_name`. Do **not** set or use `changeset.last_execution_id`; it is removed.

---

## 3. ChangeSetStatusStore → in-memory only (or separate recorder)

**File:** [tm1_git_py/changeset_status.py](tm1_git_py/changeset_status.py)

- **Option A:** Refactor `ChangeSetStatusStore` so it no longer takes `status_dir` and does not write to disk:
  - Remove `status_dir` and `self.path`; remove `_write()` and all calls to it.
  - Constructor: `__init__(self, execution_id: str, changeset_name: Optional[str] = None)`. Caller (apply) generates `execution_id` as `changeset_name + timestamp` (timestamp `yyyyMMddHHmmss`) and passes it in.
  - The store only updates `self.status` (the `ChangeSetExecutionStatus` instance) in memory. No file I/O.
- **Option B:** Keep `ChangeSetStatusStore` for backward compatibility (e.g. “write to file” use case) but add a separate in-memory-only recorder used by apply, and have apply use that. Then apply never uses `status_dir` and never instantiates a file-writing store.

Recommendation: **Option A** — single class that only builds status in memory. Callers who want to persist status can serialize `ChangeSetExecutionStatus` (e.g. via `dataclasses.asdict`) and write JSON themselves.

- **Loading from file:** The current `ChangeSetStatusStore.load(status_dir, execution_id)` reads a previously saved status from disk. Since the store no longer writes, this can be moved to a standalone function or a class method on `ChangeSetExecutionStatus`, e.g. `ChangeSetExecutionStatus.load(path: Union[str, Path])` that reads JSON and returns an instance. This preserves the ability for callers who saved status (e.g. to a path they chose) to load it back.

---

## 4. Changeset.apply() API

**File:** [tm1_git_py/changeset.py](tm1_git_py/changeset.py)

- Update `Changeset.apply()` to no longer accept `status_dir`, `execution_id`, or `changeset_name`.
- Signature: `apply(self, tm1_service, *, fail_fast: bool = True) -> ApplyResult` (or the chosen return type from apply module).
- Delegate to `apply_changeset(changeset=self, tm1_service=tm1_service, fail_fast=fail_fast)` and return the result. Do not set `last_execution_id` (removed from Changeset).

---

## 5. Serialization for callers

- **ChangeSetExecutionStatus** is already a dataclass. Callers can serialize with `dataclasses.asdict(status)` and then `json.dump()` to a file or stream.
- Optionally add a helper on `ChangeSetExecutionStatus`, e.g. `def to_dict(self) -> dict` (using asdict) and `@classmethod def load(cls, path: Path) -> ChangeSetExecutionStatus` for reading back from a JSON file, so that “save/load status” is easy for callers without tying apply to a directory.

---

## 6. Call sites to update

| Location | Current usage | Update |
|----------|----------------|--------|
| [test_integration/test_changeset_apply.py](test_integration/test_changeset_apply.py) | `apply()` helper passes `status_dir='tests'`, `execution_id='test_create_and_delete'`; expects `(success, errors)` | Call `changeset.apply(tm1_service=...)` with no status_dir/execution_id. Use new return type (e.g. `result.success`, `result.status`). If tests need to assert on or persist status, use the returned status (e.g. serialize to a temp file under `tests/` if desired). |
| [test_integration/test_process_chore_changeset_apply.py](test_integration/test_process_chore_changeset_apply.py) | Same pattern with `status_dir`, `execution_id` | Same as above. |
| [tests/test_unit.py](tests/test_unit.py) | Uses `Changeset(changeset_name="mock_changes")` and may assert on payload `changeset_name` | No change to construction (changeset_name arg remains optional in __init__; attribute is always str). Adjust any assertions that depend on apply return shape. |
| [tm1_git_py/filter.py](tm1_git_py/filter.py) | Copies `changeset.last_execution_id` to `filtered_changeset.last_execution_id` | **Remove** the line that assigns `filtered_changeset.last_execution_id = changeset.last_execution_id`; `last_execution_id` no longer exists on Changeset. |

---

## 7. Summary of API changes

- **Changeset:** Keep current `__init__(changeset_name=...)` logic. `changeset_name` is a **non-optional** attribute (type `str`). **Remove** `last_execution_id` from Changeset.
- **apply(changeset, tm1_service, \*, fail_fast=...):** No `status_dir`, `execution_id`, or `changeset_name`. Returns `ApplyResult` (or 3-tuple) including in-memory `ChangeSetExecutionStatus` when there were changes. Execution id is `changeset.changeset_name + timestamp` (yyyyMMddHHmmss).
- **ChangeSetStatusStore:** No longer takes `status_dir`; no file I/O. Used only to build `ChangeSetExecutionStatus` in memory.
- **Persistence:** Caller receives status from apply and can serialize it. Optional helper on `ChangeSetExecutionStatus` for `to_dict` / `load(path)` for convenience.

---

## 8. Implementation order

1. **changeset.py:** Keep current `changeset_name` __init__ logic; ensure `changeset_name` is typed as non-optional `str`. Remove `last_execution_id` attribute. Update `apply()` signature and delegation (no status_dir, execution_id, changeset_name).
2. **changeset_status.py:** Refactor store to in-memory only (no status_dir, no _write); add `ChangeSetExecutionStatus.load(path)` (or equivalent) for loading from file.
3. **apply.py:** Generate execution_id as `changeset.changeset_name + timestamp` (yyyyMMddHHmmss); remove status_dir, execution_id, changeset_name parameters; always build status in memory; return ApplyResult (or 3-tuple). Do not set changeset.last_execution_id.
4. **filter.py:** Remove the line that copies `changeset.last_execution_id` to `filtered_changeset.last_execution_id`.
5. **Tests:** Update integration and unit tests to use new apply API and return type; remove status_dir/execution_id from apply calls; remove any use of last_execution_id; adjust assertions if they depend on status file or return shape.
6. **Docs / CLI:** If main.py or docs mention status_dir or execution_id for apply, update them to describe the new behavior (status in response; caller may serialize).

This plan keeps status tracking semantics (execution_id, changeset_name, per-operation log, success/failure) while moving persistence to the caller and simplifying the apply API.

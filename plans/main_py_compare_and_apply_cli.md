---
name: main.py compare and apply CLI
overview: Add compare and apply CLI commands to main.py using subparsers, with compare producing a changeset file from two model folders and apply applying a changeset file to a TM1 server.
todos: []
isProject: false
---

# main.py CLI upgrades: compare and apply

## Current state

- [tm1_git_py/main.py](tm1_git_py/main.py) uses a single parser with `command` in `['export', 'filter', 'compare']`; only **export** and **filter** are implemented. **compare** is in choices but has no handler.
- Shared flags: `-s/--server`, `-m/--model_folder`, `-mo/--model_output_folder`, `-o/--overwrite`, `-f/--filter` are global; some only apply to specific commands.
- **Comparator:** [tm1_git_py/comparator.py](tm1_git_py/comparator.py) — `Comparator().compare(model1, model2, mode='full'|'add_only', filter_rules=...)` returns a `Changeset`.
- **Changeset:** [tm1_git_py/changeset.py](tm1_git_py/changeset.py) — `changeset.export(file_path)` writes YAML; `changeset.apply(tm1_service, status_dir=..., execution_id=..., fail_fast=...)` delegates to [tm1_git_py/apply.py](tm1_git_py/apply.py). `import_changeset(changeset_file)` loads a Changeset from YAML/JSON.

---

## Recommended approach: subparsers

Use **subparsers** so each command has its own arguments and help text. Shared options (e.g. `--filter`) can be added only to the subparsers that need them.

```text
python tm1_git_py/main.py export ...
python tm1_git_py/main.py filter ...
python tm1_git_py/main.py compare ...
python tm1_git_py/main.py apply ...
```

---

## 1. Compare command

**Purpose:** Compare two model folders and write a changeset (add/remove/modify) to a file.

**Arguments:**

| Argument          | Required          | Default                                | Description                                                       |
| ----------------- | ----------------- | -------------------------------------- | ----------------------------------------------------------------- |
| `--source` / `-s` | Yes (for compare) | —                                      | Path to "source" or "base" model folder (e.g. Git branch A).      |
| `--target` / `-t` | Yes (for compare) | —                                      | Path to "target" model folder (e.g. Git branch B).                |
| `--output` / `-o` | No                | `changeset.yaml` (or `changeset.json`) | Path for the output changeset file.                               |
| `--mode`          | No                | `full`                                 | `full` (add + remove + modify) or `add_only` (add + modify only). |
| `--filter` / `-f` | No                | —                                      | Path to filter rules file; applied to both models before compare. |
| `--format`        | No                | `yaml`                                 | Output format: `yaml` or `json`.                                  |

**Flow:**

1. Resolve and validate `--source` and `--target` (must exist and be directories).
2. `deserialize_model(source)` and `deserialize_model(target)`.
3. If `--filter` is set, load filter rules and apply `filter(model, filter_rules)` to both models.
4. `comparator = Comparator(use_default_filter=True)` (or expose `--no-default-filter` if desired), then `changeset = comparator.compare(model_source, model_target, mode=args.mode, filter_rules=filter_rules)`.
5. Write changeset: use `changeset.export(output_path)` for YAML (already exists), or build payload with `changeset.to_json()` and write JSON when `--format json`. Create parent dirs if needed.
6. Print summary (e.g. number of add/remove/modify) and output path.

**Implementation notes:**

- Reuse the same filter-loading logic as in `_filter()` (read file, strip comments, list of rules). Pass the list to `compare(..., filter_rules=...)` when `--filter` is given; otherwise `filter_rules=None`.
- Naming: avoid overloading `-s` with `--server` (export/apply) and `--source` (compare). Prefer long names `--source` / `--target` for compare and reserve `-s` for `--server` on export/apply, or use `-s` for source only in the compare subparser.

---

## 2. Apply command

**Purpose:** Load a changeset from a file and apply it to a TM1 server.

**Arguments:**

| Argument             | Required        | Default                       | Description                                                                          |
| -------------------- | --------------- | ----------------------------- | ------------------------------------------------------------------------------------ |
| `--server` / `-s`    | Yes (for apply) | —                             | TM1 server name from config (e.g. from `tm1servers.yaml`).                           |
| `--changeset` / `-c` | Yes (for apply) | —                             | Path to changeset YAML or JSON file.                                                 |
| `--status-dir`       | No              | e.g. `.` or `status`          | Directory to write execution status JSON.                                            |
| `--execution-id`     | No              | Auto (e.g. UUID or timestamp) | Execution ID for status file naming.                                                 |
| `--no-fail-fast`     | No              | False                         | If set, continue applying remaining changes after a failure (log and report at end). |

**Flow:**

1. Resolve `--changeset` path; validate file exists.
2. Load TM1 connection: `_tm1_connection(args.server)` (reuse existing helper).
3. Load changeset: `changeset = import_changeset(args.changeset)` from [tm1_git_py/changeset.py](tm1_git_py/changeset.py).
4. Call `changeset.apply(tm1_service, status_dir=args.status_dir, execution_id=args.execution_id, fail_fast=not args.no_fail_fast)`.
5. Print success or failure and, if status_dir is set, the status file path.

**Implementation notes:**

- `import_changeset` already supports both YAML and JSON (tries JSON first, then YAML). No extra handling needed.
- If `status_dir` is omitted, pass `None` so apply does not write a status file (current API supports this).

---

## 3. main.py structure (subparsers)

- **Parser:** `argparse.ArgumentParser(description="...")` with `subparsers = parser.add_subparsers(dest='command', required=True)`.
- **export:** `sub = subparsers.add_parser('export', help='Export model from TM1')` with `--server` (required), `--model_output_folder` / `-mo`, `--overwrite`, `--filter` / `-f`. Handler: current export logic.
- **filter:** `sub = subparsers.add_parser('filter', help='Filter a model on disk')` with `--model_folder` / `-m`, `--model_output_folder` / `-mo`, `--overwrite`, `--filter` / `-f`. Handler: current filter logic.
- **compare:** `sub = subparsers.add_parser('compare', help='Compare two model folders and output a changeset')` with `--source`, `--target`, `--output`, `--mode`, `--filter`, `--format`. Handler: compare flow above.
- **apply:** `sub = subparsers.add_parser('apply', help='Apply a changeset to a TM1 server')` with `--server`, `--changeset`, `--status-dir`, `--execution-id`, `--no-fail-fast`. Handler: apply flow above.

Shared helpers (`_tm1_connection`, `_prepare_model_folder`, `_filter`) stay; add a small helper to load filter rules from a file path if not already factored (e.g. `filter.import_filter` exists and can be used for compare/filter).

---

## 4. File changes

- **[tm1_git_py/main.py](tm1_git_py/main.py):**
  - Replace single-command parser with subparsers for `export`, `filter`, `compare`, `apply`.
  - Implement `cmd_compare(args)` and `cmd_apply(args)` (or inline in `main()` with `if args.command == 'compare':` etc.).
  - For compare: import `Comparator` and `import_changeset` is not needed; for apply: import `import_changeset` from `tm1_git_py.changeset`. Use `Changeset.export` for YAML; for JSON output in compare, use `changeset.to_json()` and write with `json.dump` (and create output dir if needed).
  - Ensure `compare` and `apply` report errors (e.g. file not found, deserialize errors) with clear messages and non-zero exit where appropriate.

No changes to comparator, changeset, or apply modules are required; the CLI only wires existing APIs.

---

## 5. Example usage (after implementation)

```bash
# Compare two model folders, write changeset to default path
python tm1_git_py/main.py compare --source model_v1 --target model_v2

# Compare with filter and add_only mode, output JSON
python tm1_git_py/main.py compare --source model_v1 --target model_v2 --filter rules.txt --mode add_only --output diff.json --format json

# Apply a changeset to server "dev", write status to status/
python tm1_git_py/main.py apply --server dev --changeset changeset.yaml --status-dir status
```

---

## 6. Optional follow-ups

- **compare:** Add `--no-default-filter` to disable `Comparator.DEFAULT_FILTER_RULES` when comparing (for power users).
- **apply:** Add `--dry-run` that loads the changeset and validates or prints what would be applied without calling TM1 (if validation is re-enabled in apply).
- **Shared:** Consider a single `--config` override for tm1servers path for all commands that need a server (export, apply).

This plan keeps the existing export/filter behavior, adds compare and apply with clear arguments and one place to maintain (main.py).

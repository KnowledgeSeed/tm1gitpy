import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
import tracemalloc

from TM1py import TM1Service

from tm1_git_py.changeset import import_changeset
from tm1_git_py.comparator import Comparator
from tm1_git_py.config import TM1ServersConfig
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.exporter import export
from tm1_git_py.filter import filter, import_filter
from tm1_git_py.logging_config import setup_logging
from tm1_git_py.model import Model
from tm1_git_py.serializer import serialize_model


logger = logging.getLogger(__name__)


def _tm1_connection(server_name: str) -> TM1Service:
    config = TM1ServersConfig()
    config.load()
    return _tm1_connection_from_config(config, server_name)


def _tm1_connection_from_config(config: TM1ServersConfig, server_name: str) -> TM1Service:
    server_config = config.get(server_name)
    logger.debug(
        "Creating TM1 connection for server='%s' base_url='%s' user='%s'",
        server_name,
        server_config.base_url,
        server_config.user,
    )

    tm1 = TM1Service(
        base_url=server_config.base_url,
        user=server_config.user,
        password=server_config.password or ""
    )
    return tm1


def _prepare_model_folder(model_folder: str, overwrite: bool = False):
    model_path = Path(model_folder)
    if model_path.exists() and model_path.is_dir():
        if not overwrite:
            logger.error(
                "Model folder '%s' already exists. Use --overwrite flag to clear and overwrite.",
                model_folder,
            )
            sys.exit(1)
        logger.info("Clearing existing model folder: %s", model_folder)
        shutil.rmtree(model_folder)


def _load_filter_rules(filter_file: str | None) -> list[str]:
    if not filter_file:
        return []
    filter_path = Path(filter_file)
    if not filter_path.exists():
        logger.error("Filter file '%s' not found.", filter_file)
        sys.exit(1)
    try:
        filter_rules = import_filter(str(filter_path))
        logger.info("Loaded %d filter rule(s) from: %s", len(filter_rules), filter_file)
        return filter_rules
    except Exception:
        logger.exception("Error loading filter from: %s", filter_file)
        sys.exit(1)


def _filter(model, filter_rules: list[str]) -> Model:
    if filter_rules:
        logger.info("Applying %d filter rule(s)", len(filter_rules))
        try:
            filtered_model = filter(model, filter_rules)
            logger.info("Filter applied successfully")
            return filtered_model
        except Exception:
            logger.exception("Error applying filter rules")
            sys.exit(1)

    logger.debug("No filter rules provided, skipping filtering")
    return model


def _add_log_level(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set log level (overrides TM1GITPY_LOG_LEVEL)",
    )


def _cmd_export(args: argparse.Namespace) -> None:
    tm1_service = _tm1_connection(args.server)
    model_output_folder = args.model_output_folder or "export"

    _prepare_model_folder(model_output_folder, args.overwrite)

    filter_rules = _load_filter_rules(args.filter)

    logger.info("Exporting model to folder: %s", model_output_folder)
    internal_model_dir = str(Path(model_output_folder))
    exported_model, export_errors = export(
        tm1_service,
        filter_rules,
        internal_model_dir=internal_model_dir,
    )

    if export_errors and any(export_errors.values()):
        logger.warning("Export errors encountered")
        for error_type, errors in export_errors.items():
            if errors:
                logger.warning("Export error category=%s details=%s", error_type, errors)
    else:
        logger.info("Export completed successfully with no errors")

    serialize_model(exported_model, model_output_folder)
    logger.info("Model serialized to: %s", model_output_folder)


def _cmd_filter(args: argparse.Namespace) -> None:
    model_folder = args.model_folder or "export"
    model_output_folder = args.model_output_folder or "export"
    logger.info("Loading model from folder: %s", model_folder)

    _prepare_model_folder(model_output_folder, args.overwrite)
    model, errors = deserialize_model(model_folder)
    if errors:
        logger.warning("Deserialization completed with %d error(s)", len(errors))

    filter_rules = _load_filter_rules(args.filter)
    filtered_model = _filter(model, filter_rules)

    serialize_model(filtered_model, model_output_folder)
    logger.info("Model serialized to: %s", model_output_folder)


def _cmd_compare(args: argparse.Namespace) -> None:
    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    if not source.is_dir():
        logger.error("Source model path is not a directory: %s", source)
        sys.exit(1)
    if not target.is_dir():
        logger.error("Target model path is not a directory: %s", target)
        sys.exit(1)

    logger.info("Loading source model from %s", source)
    model_source, err_source = deserialize_model(str(source))
    if err_source:
        logger.warning("Source deserialization reported %d error(s)", len(err_source))

    logger.info("Loading target model from %s", target)
    model_target, err_target = deserialize_model(str(target))
    if err_target:
        logger.warning("Target deserialization reported %d error(s)", len(err_target))

    extra_filter = _load_filter_rules(args.filter) if args.filter else None

    comparator = Comparator()
    changeset = comparator.compare(
        model_source,
        model_target,
        mode=args.mode,
        filter_rules=extra_filter,
    )

    out = args.output
    if not out:
        out = "changeset.yaml" if args.format == "yaml" else "changeset.json"
    output_path = Path(out).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "json":
        payload = changeset.to_json()
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote JSON changeset (%d change(s)) to %s", len(changeset.changes), output_path)
    else:
        changeset.export(output_path)
        logger.info("Wrote YAML changeset (%d change(s)) to %s", len(changeset.changes), output_path)


def _cmd_apply(args: argparse.Namespace) -> None:
    changeset_path = Path(args.changeset).expanduser().resolve()
    if not changeset_path.is_file():
        logger.error("Changeset file not found: %s", changeset_path)
        sys.exit(1)

    tm1_service = _tm1_connection(args.server)
    changeset = import_changeset(changeset_path)

    status_dir = Path(args.status_dir).expanduser().resolve() if args.status_dir else None
    ok, errors = changeset.apply(
        tm1_service,
        status_dir=status_dir,
        execution_id=args.execution_id,
        fail_fast=not args.no_fail_fast,
    )
    if ok:
        logger.info("Apply finished successfully")
    else:
        logger.error("Apply finished with failures: %s", errors)
        sys.exit(1)


def main():
    tracemalloc.start()
    parser = argparse.ArgumentParser(description="TM1 Git Py - TM1 model export, filter, compare, and apply")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export model from TM1 to a folder")
    _add_log_level(p_export)
    p_export.add_argument("-s", "--server", type=str, required=True, help="TM1 server name from tm1servers config")
    p_export.add_argument(
        "-mo", "--model_output_folder",
        type=str,
        default="export",
        help="Folder to write the serialized model",
    )
    p_export.add_argument("-o", "--overwrite", action="store_true", help="Clear output folder if it already exists")
    p_export.add_argument("-f", "--filter", type=str, help="Path to filter rules file for export")
    p_export.set_defaults(handler=_cmd_export)

    p_filter = sub.add_parser("filter", help="Load a model folder, apply filter rules, write output folder")
    _add_log_level(p_filter)
    p_filter.add_argument("-m", "--model_folder", type=str, default="export", help="Input model folder")
    p_filter.add_argument(
        "-mo", "--model_output_folder",
        type=str,
        default="export",
        help="Output folder for filtered model",
    )
    p_filter.add_argument("-o", "--overwrite", action="store_true", help="Clear output folder if it already exists")
    p_filter.add_argument("-f", "--filter", type=str, help="Path to filter rules file")
    p_filter.set_defaults(handler=_cmd_filter)

    p_compare = sub.add_parser("compare", help="Compare two model folders and write a changeset file")
    _add_log_level(p_compare)
    p_compare.add_argument(
        "--source",
        type=str,
        required=True,
        help="Base / old model directory (e.g. Git branch A)",
    )
    p_compare.add_argument(
        "--target",
        type=str,
        required=True,
        help="New model directory (e.g. Git branch B)",
    )
    p_compare.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Output changeset path (default: changeset.yaml or changeset.json by --format)",
    )
    p_compare.add_argument(
        "--mode",
        type=str,
        choices=["full", "add_only"],
        default="full",
        help="full: add/remove/modify; add_only: add/modify only",
    )
    p_compare.add_argument(
        "-f", "--filter",
        type=str,
        help="Optional filter rules file; rules are passed to the comparator for both models",
    )
    p_compare.add_argument(
        "--format",
        type=str,
        choices=["yaml", "json"],
        default="yaml",
        help="Changeset output format",
    )
    p_compare.set_defaults(handler=_cmd_compare)

    p_apply = sub.add_parser("apply", help="Apply a changeset file to a TM1 server")
    _add_log_level(p_apply)
    p_apply.add_argument("-s", "--server", type=str, required=True, help="TM1 server name from tm1servers config")
    p_apply.add_argument(
        "-c", "--changeset",
        type=str,
        required=True,
        help="Path to changeset YAML or JSON file",
    )
    p_apply.add_argument(
        "--status-dir",
        type=str,
        default=None,
        help="Directory for execution status files (optional)",
    )
    p_apply.add_argument("--execution-id", type=str, default=None, help="Execution id for status tracking")
    p_apply.add_argument(
        "--no-fail-fast",
        action="store_true",
        help="Continue applying after a failed change",
    )
    p_apply.set_defaults(handler=_cmd_apply)

    args = parser.parse_args()
    setup_logging(args.log_level)
    logger.info("Command started: %s", args.command)
    args.handler(args)
    logger.info("Command finished: %s", args.command)


if __name__ == "__main__":
    main()

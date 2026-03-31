import argparse
import logging
import shutil
import sys
from pathlib import Path
import tracemalloc
from TM1py import TM1Service
from tm1_git_py.exporter import export
from tm1_git_py.config import TM1ServersConfig
from tm1_git_py.serializer import serialize_model
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.model import Model
from tm1_git_py.filter import filter, import_filter
from tm1_git_py.logging_config import setup_logging


logger = logging.getLogger(__name__)


def _tm1_connection(server_name: str) -> TM1Service:
    config = TM1ServersConfig()
    config.load()
    return _tm1_connection_from_config(config, server_name)


def _tm1_connection_from_config(config : TM1ServersConfig, server_name: str) -> TM1Service:
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
    # Check if export folder exists
    model_path = Path(model_folder)
    if model_path.exists() and model_path.is_dir():
        if not overwrite:
            logger.error(
                "Model folder '%s' already exists. Use --overwrite flag to clear and overwrite.",
                model_folder,
            )
            sys.exit(1)
        # Clear the model folder
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
    # Apply filter if specified
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


def main():
    tracemalloc.start()
    parser = argparse.ArgumentParser(description="TM1 Git Py - TM1 Model Version Control Tool")
    parser.add_argument('command', type=str, choices=['export', 'filter', 'compare'], help="Command to execute")
    parser.add_argument('-s', '--server', type=str, help="TM1 server to use from tm1servers.yaml")
    parser.add_argument('-m', '--model_folder', type=str, default='export', help="Folder to reference model for export and filter")
    parser.add_argument('-mo', '--model_output_folder', type=str, default='export', help="Folder to output filtered model")
    parser.add_argument('-o', '--overwrite', action='store_true', help="Overwrite existing export folder (clears folder if exists)")
    parser.add_argument('-f', '--filter', type=str, help="filter.txt file location for export")
    parser.add_argument(
        '--log-level',
        type=str,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help="Set log level (overrides TM1GITPY_LOG_LEVEL)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger.info("Command started: %s", args.command)
    filter_rules = _load_filter_rules(args.filter)

    if args.command == 'export':
        tm1_service = _tm1_connection(args.server)
        model_output_folder = args.model_output_folder or 'export'

        _prepare_model_folder(model_output_folder, args.overwrite)

        logger.info("Exporting model to folder: %s", model_output_folder)
        internal_model_dir = str(Path(model_output_folder))
        exported_model, export_errors = export(
            tm1_service,
            filter_rules,
            internal_model_dir=internal_model_dir,
        )

        # Print any export errors
        if export_errors and any(export_errors.values()):
            logger.warning("Export errors encountered")
            for error_type, errors in export_errors.items():
                if errors:
                    logger.warning("Export error category=%s details=%s", error_type, errors)
        else:
            logger.info("Export completed successfully with no errors")

        # exported_model = _filter(exported_model, filter_rules)

        serialize_model(exported_model, model_output_folder)
        logger.info("Model serialized to: %s", model_output_folder)

    elif args.command == 'filter':
        model_folder = args.model_folder or 'export'
        model_output_folder = args.model_output_folder or 'export'
        logger.info("Loading model from folder: %s", model_folder)

        _prepare_model_folder(model_output_folder, args.overwrite)
        model, errors = deserialize_model(model_folder)
        if errors:
            logger.warning("Deserialization completed with %d error(s)", len(errors))

        filtered_model = _filter(model, filter_rules)

        serialize_model(filtered_model, model_output_folder)
        logger.info("Model serialized to: %s", model_output_folder)

    logger.info("Command finished: %s", args.command)

if __name__ == '__main__':
    main()

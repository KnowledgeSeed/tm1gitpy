import argparse
import os
import shutil
import sys
from pathlib import Path
from TM1py import TM1Service
from tm1_git_py.exporter import Chore, export
from tm1_git_py.config import TM1ServersConfig, TM1ServerConfig
from tm1_git_py.serializer import serialize_model
from tm1_git_py.deserializer import deserialize_model
from tm1_git_py.model import Model
from tm1_git_py.filter import filter


def _tm1_connection(server_name: str) -> TM1Service:

    config = TM1ServersConfig()
    config.load()
    return _tm1_connection_from_config(config, server_name)


def _tm1_connection_from_config(config : TM1ServersConfig, server_name: str) -> TM1Service:

    server_config = config.get(server_name)

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
            print(f"Error: Model folder '{model_folder}' already exists. Use --overwrite flag to clear and overwrite.", file=sys.stderr)
            sys.exit(1)
        else:
            # Clear the model folder
            print(f"Clearing existing model folder: {model_folder}")
            shutil.rmtree(model_folder)

def _filter(model, filter_file) -> Model:
    # Apply filter if specified
    if filter_file:
        filter_path = Path(filter_file)
        if not filter_path.exists():
            print(f"Error: Filter file '{filter_file}' not found.", file=sys.stderr)
            sys.exit(1)

        print(f"Applying filter from: {filter_file}")
        try:
            with open(filter_path, 'r', encoding='utf-8') as f:
                filter_rules = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]

            print(f"Loaded {len(filter_rules)} filter rules")
            filtered_model = filter(model, filter_rules)
            print("Filter applied successfully")
            return filtered_model
        except Exception as e:
            print(f"Error applying filter: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        return model


def main():
    parser = argparse.ArgumentParser(description="TM1 Git Py - TM1 Model Version Control Tool")
    parser.add_argument('command', type=str, choices=['export', 'filter', 'compare'], help="Command to execute")
    parser.add_argument('-s', '--server', type=str, help="TM1 server to use from tm1servers.yaml")
    parser.add_argument('-m', '--model_folder', type=str, default='export', help="Folder to reference model for export and filter")
    parser.add_argument('-mo', '--model_output_folder', type=str, default='export', help="Folder to output filtered model")
    parser.add_argument('-o', '--overwrite', action='store_true', help="Overwrite existing export folder (clears folder if exists)")
    parser.add_argument('-f', '--filter', type=str, help="filter.txt file location for export")
    args = parser.parse_args()

    if args.command == 'export':
        tm1_service = _tm1_connection(args.server)
        model_output_folder = args.model_output_folder or 'export'

        _prepare_model_folder(model_output_folder, args.overwrite)

        print(f"Exporting model to folder: {model_output_folder}")
        exported_model, export_errors = export(tm1_service)

        # Print any export errors
        if export_errors and any(export_errors.values()):
            print("Export errors encountered:")
            for error_type, errors in export_errors.items():
                if errors:
                    print(f"  {error_type}: {errors}")
        else:
            print("Export completed successfully with no errors")

        exported_model = _filter(exported_model, args.filter)

        serialize_model(exported_model, model_output_folder)
        print(f"Model serialized to: {model_output_folder}")

    elif args.command == 'filter':
        model_folder = args.model_folder or 'export'
        model_output_folder = args.model_output_folder or 'export'
        print(f"Loading model from folder: {model_folder}")

        _prepare_model_folder(model_output_folder, args.overwrite)
        model, errors = deserialize_model(model_folder)

        filtered_model = _filter(model, args.filter)

        serialize_model(filtered_model, model_output_folder)
        print(f"Model serialized to: {model_output_folder}")

if __name__ == '__main__':
    main()

import argparse
import os
import shutil
import sys
from pathlib import Path
from TM1py import TM1Service
from exporter import export
from config import TM1ServersConfig
from serializer import serialize_model
from deserializer import deserialize_model
from comparator import Comparator


def tm1_connection(server_name: str) -> TM1Service:
    """Creates a TM1 connection before tests and closes it after all tests."""
    
    config = TM1ServersConfig()
    config.load()
    server_config = config.get(server_name)
    
    tm1 = TM1Service(
        base_url=server_config.base_url,
        user=server_config.user,
        password=server_config.password or ""
    )
    return tm1


parser = argparse.ArgumentParser(description="TM1 Git Py - TM1 Model Version Control Tool")
parser.add_argument('command', type=str, choices=['export', 'import', 'compare'], help="Command to execute")
parser.add_argument('-s', '--server', type=str, help="TM1 server to use from tm1servers.yaml")
parser.add_argument('-e', '--export_folder', type=str, default='export', help="Folder to export the model to or import from (default: 'export')")
parser.add_argument('-o', '--overwrite', action='store_true', help="Overwrite existing export folder (clears folder if exists)")
args = parser.parse_args()

tm1_service = tm1_connection(args.server)
export_folder = args.export_folder or 'export'

# Check if export folder exists
export_path = Path(export_folder)
if export_path.exists() and export_path.is_dir():
    if not args.overwrite:
        print(f"Error: Export folder '{export_folder}' already exists. Use --overwrite flag to clear and overwrite.", file=sys.stderr)
        sys.exit(1)
    else:
        # Clear the export folder
        print(f"Clearing existing export folder: {export_folder}")
        shutil.rmtree(export_folder)

print(f"Exporting model to folder: {export_folder}")
exported_model, export_errors = export(tm1_service)

# Print any export errors
if export_errors and any(export_errors.values()):
    print("Export errors encountered:")
    for error_type, errors in export_errors.items():
        if errors:
            print(f"  {error_type}: {errors}")
else:
    print("Export completed successfully with no errors")

serialize_model(exported_model, export_folder)
print(f"Model serialized to: {export_folder}")

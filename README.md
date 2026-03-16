# tm1gitpy

A utility for exporting and comparing TM1 models in Git-friendly formats, enabling version control workflows for IBM Planning Analytics/TM1 models.

## Overview

`tm1gitpy` allows you to:
- Export TM1 models (cubes, dimensions, processes, chores) to a structured folder format
- Filter exports to include only specific objects
- Serialize models to Git-friendly formats for version control
- Compare model versions to track changes (Python API)

## Installation

### From Source

To **use** the package (runtime dependencies only):

```bash
git clone <repository-url>
cd tm1_git_py
pip install -e .
```

Or install from a requirements file: `pip install -r requirements.txt` then `pip install -e .`

To **run tests** or develop (runtime + test dependencies):

```bash
pip install -r requirements-dev.txt
# or
pip install -e ".[dev]"
```

### Requirements

- Python 3.10 or higher
- TM1py >= 2.1, < 3.0
- requests >= 2.25
- PyYAML >= 6.0

## Configuration

Create a configuration file at `.tm1gitpy/tm1servers.yaml` (local directory) or `~/.tm1gitpy/tm1servers.yaml` (user home):

```yaml
servers:
  dev:
    base_url: http://localhost:12354/api/v1/
    user: admin
    password: your_password  # Optional - can use environment variables
  
  prod:
    base_url: https://prod-server.company.com:12354/api/v1/
    user: admin
    password: ${TM1_PROD_PASSWORD}  # Environment variable placeholder
```

## Usage

### Export TM1 Model

Export a full TM1 model from a server:

```bash
python tm1_git_py/main.py export --server dev --model_output_folder model_dir --overwrite
```

### Filter Model

Apply filters to include only specific objects:

```bash
python tm1_git_py/main.py filter --filter examples/filter.txt --model_folder model_dir --model_output_folder model_dir_filtered --overwrite
```

Filter file format (one pattern per line, `#` for comments):

```
# Include specific dimensions
}Dimensions/}TimeDay
}Dimensions/}TimePeriods

# Include all cubes starting with "Sales"
}Cubes/Sales*

# Include specific processes
}Processes/bedrock.*
```

### Command-Line Arguments

```
python tm1_git_py/main.py <command> [options]

Commands:
  export    Export TM1 model from server
  filter    Filter an existing model export
  compare   Compare two model versions

Options:
  -s, --server SERVER           TM1 server name from tm1servers.yaml
  -m, --model_folder FOLDER     Input model folder (default: export)
  -mo, --model_output_folder    Output model folder (default: export)
  -o, --overwrite              Overwrite existing folder
  -f, --filter FILE            Filter file path
  --log-level LEVEL            Log level: DEBUG, INFO, WARNING, ERROR
```

Logging defaults to `INFO`. You can also set `TM1GITPY_LOG_LEVEL` in the environment; `--log-level` takes precedence.

## Examples

See the [examples](examples/) directory for usage examples:
- [config_usage.py](examples/config_usage.py) - Server configuration examples
- [filter.txt](examples/filter.txt) - Filter pattern examples

For model comparison and changeset workflows, use the Python API (`tm1_git_py.comparator`, `tm1_git_py.changeset`, `tm1_git_py.apply`).

## Building Binary

Build a standalone executable using Nuitka:

```bash
python -m nuitka tm1_git_py/main.py --follow-imports --no-deployment-flag=self-execution --mode=onefile --include-module=ijson.backends.yajl2_c --output-filename=tm1gitpy
```

## Development

### Running Tests

```bash
pytest tests/
```

Integration tests (TM1 container/local TM1 required):

```bash
PYTHONPATH=. pytest test_integration/
```

### Project Structure

```
tm1_git_py/
├── tm1_git_py/          # Main package
│   ├── main.py          # CLI entry point
│   ├── config.py        # Server configuration
│   ├── exporter.py      # TM1 model export
│   ├── serializer.py    # Model serialization
│   ├── deserializer.py  # Model deserialization
│   ├── filter.py        # Object filtering
│   ├── comaprator.py    # Compare TM1 models
│   ├── changeset.py     # Build changeset
│   ├── apply.py         # Apply changeset
│   └── model/           # Model data structures
├── examples/            # Usage examples
├── docs/               # Documentation
└── tests/              # Test suite
```

## License


See LICENSE file for details.

# Refactor summary: Eliminate tm1_bedrock_py dependency

## Overview

The `tm1_bedrock_py` package was removed from the project as a dependency **to limit initial scope**. It was not used anywhere in active code (only commented references in the cube module). **It will be re-added in a later release** when the cube-recreate / data-copy logic that depends on it is enabled.

## Rationale

- **Scope:** Removing it for now keeps the initial release simpler and avoids pulling in transitive dependencies (e.g. pandas, pyodbc, sqlalchemy) until the feature that needs tm1_bedrock_py is implemented.
- **No active usage today:** The codebase had no `import tm1_bedrock_py` in use. The only reference was in `tm1_git_py/model/cube.py`: a commented import and a comment describing cube-recreate logic that would use tm1-bedrock-py’s `data_copy_intercube`. That logic is planned for a later release.

## Changes made

| File | Change |
|------|--------|
| **requirements.txt** | Removed `tm1_bedrock_py>=1.1.4`. Runtime deps are now: TM1py, requests, PyYAML. |
| **pyproject.toml** | Removed `tm1_bedrock_py>=1.1.4` from `[project] dependencies`. |
| **setup.py** | Removed `tm1_bedrock_py>=1.1.4` from `install_requires`. Added `PyYAML>=6.0` so setup.py stays aligned with other dependency lists. |
| **README.md** | Removed `tm1_bedrock_py >= 1.1.4` from the Requirements section. |

## What was not changed

- **tm1_git_py/model/cube.py:** The commented import and the comment about tm1-bedrock-py were left in place for the planned later release that will re-add the dependency and enable the cube-recreate / data-copy logic.

## Resulting runtime dependencies

- TM1py >= 2.1, < 3.0  
- requests >= 2.25  
- PyYAML >= 6.0  

No code changes were required; the package was only referenced in dependency declarations and documentation.

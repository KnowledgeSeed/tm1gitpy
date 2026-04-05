import logging
import os
import time
from pathlib import Path
from typing import Optional


DEFAULT_LOG_LEVEL_NAME = "INFO"
_ALLOWED_LEVEL_NAMES = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _resolve_level_name(level: Optional[str], env_var: str) -> str:
    if level is not None:
        candidate = str(level).strip().upper()
    else:
        candidate = os.getenv(env_var, DEFAULT_LOG_LEVEL_NAME).strip().upper()

    if candidate not in _ALLOWED_LEVEL_NAMES:
        return DEFAULT_LOG_LEVEL_NAME
    return candidate


def _resolve_log_file_path(log_file: Optional[str], command_name: Optional[str] = None) -> Optional[str]:
    if not log_file:
        return None
    raw = str(log_file).strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    command = (command_name or "run").strip() or "run"
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path.resolve())
    path.mkdir(parents=True, exist_ok=True)
    return str((path / f"{command}.{timestamp}.log").resolve())


def setup_logging(
    level: Optional[str] = None,
    env_var: str = "TM1GITPY_LOG_LEVEL",
    *,
    enable_console: bool = True,
    log_file: Optional[str] = None,
    command_name: Optional[str] = None,
) -> int:
    level_name = _resolve_level_name(level=level, env_var=env_var)
    level_no = getattr(logging, level_name, logging.INFO)
    log_path = _resolve_log_file_path(log_file, command_name=command_name)

    root_logger = logging.getLogger()
    root_logger.setLevel(level_no)

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Remove existing handlers so CLI/main can explicitly control console/file destinations.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level_no)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if log_path:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level_no)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        root_logger.info("Execution log file: %s", log_path)

    return level_no

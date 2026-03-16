import logging
import os
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


def setup_logging(level: Optional[str] = None, env_var: str = "TM1GITPY_LOG_LEVEL") -> int:
    level_name = _resolve_level_name(level=level, env_var=env_var)
    level_no = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level_no)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(handler)

    return level_no

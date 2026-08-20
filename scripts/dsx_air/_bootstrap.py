"""Ensure scripts/ is on sys.path for air_common and env_config imports."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = SCRIPTS_DIR.parent


def repo_root() -> Path:
    return _REPO_ROOT


def ensure_scripts_path() -> None:
    scripts = str(SCRIPTS_DIR)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)

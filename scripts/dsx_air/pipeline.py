"""Run numbered install scripts with the current process environment."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dsx_air._bootstrap import repo_root


def run_script(name: str, *args: str) -> None:
    scripts = repo_root() / "scripts"
    cmd = [sys.executable, str(scripts / name), *args]
    print(f"+ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(scripts), env=os.environ, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{name} exited with status {result.returncode}")


def cache_dir() -> Path:
    path = repo_root() / ".cache"
    path.mkdir(parents=True, exist_ok=True)
    return path

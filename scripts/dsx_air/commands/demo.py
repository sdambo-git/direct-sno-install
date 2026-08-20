from __future__ import annotations

from dsx_air.commands import cluster, operators, status


def run_demo() -> int:
    code = status.run_status(compact=True)
    if code != 0:
        return code
    code = cluster.run_cluster()
    operators.run_operators()
    return code

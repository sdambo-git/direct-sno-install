"""Recover topology nodes to discovery."""
from __future__ import annotations

from pathlib import Path

from dsx_air.pipeline import cache_dir, run_script
from dsx_air.spec import apply_to_environ, load_spec, preflight_auth
from dsx_air.topology import node_names, write_manifest


def run_recover(
    *,
    spec_path: Path | None = None,
    node: str | None = None,
    reset_ai: bool = False,
) -> int:
    if spec_path is not None:
        spec = load_spec(spec_path)
        preflight_auth(spec)
        topo = cache_dir() / spec.simulation.name / "topology.json"
        if not topo.is_file():
            write_manifest(spec, topo, cdrom="dsxair-discovery-iso")
        apply_to_environ(spec, topology_path=topo)
        targets = [node] if node else node_names(spec)
    else:
        targets = [node] if node else [None]

    for name in targets:
        args: list[str] = []
        if name:
            args.extend(["--node", name])
        if reset_ai:
            args.append("--reset-ai")
        run_script("09_recover_to_discovery.py", *args)
    return 0

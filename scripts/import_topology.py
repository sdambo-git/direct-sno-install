#!/usr/bin/env python3
"""
Step 3 of ../README.md — import a raw Air simulation manifest (default
../topology.json) and start it, now that the images it references already
exist (see upload_discovery_iso.py / upload_blank_disk.py).

Run:
    uv run import_topology.py
    uv run import_topology.py ../Topology-NoDPU.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from air_common import ensure_jump_host_ready, jump_host_ssh_command, wait_for_sim_state
from upload_discovery_iso import get_api  # reuse existing auth config

DEFAULT_TOPOLOGY_PATH = Path(__file__).resolve().parent.parent / "topology.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=DEFAULT_TOPOLOGY_PATH,
        help=f"Simulation manifest to import (default: {DEFAULT_TOPOLOGY_PATH}).",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Return right after import instead of polling until the simulation is ACTIVE.",
    )
    args = parser.parse_args()

    api = get_api()

    print(f"Importing {args.manifest} and starting the simulation ...")
    sim = api.simulations.import_from_simulation_manifest(
        simulation_manifest=args.manifest,
        attempt_start=True,
    )
    print(f"Simulation created: id={sim.id} name={sim.name!r} state={sim.state!r}")

    if args.no_wait:
        print(
            "Watch it boot from the node console in the Air UI, then continue "
            "with Step 4 (Assisted Installer host discovery)."
        )
        return

    wait_for_sim_state(sim, "ACTIVE", timeout=900)

    print("\nNodes in the simulation:")
    for node in sim.nodes.list():
        print(f"  - {node.name} (state={node.state})")

    print("\nSetting up the oob-mgmt-server jump host ...")
    try:
        service, server = ensure_jump_host_ready(sim)
    except SystemExit as exc:
        print(f"  skipped: {exc} (run bootstrap_jump_host.py once the "
              "simulation is ACTIVE to set this up later)")
        return
    print(f"Jump host ready: {server.name!r} reachable via:\n\n    {jump_host_ssh_command(service, server)}\n")


if __name__ == "__main__":
    main()

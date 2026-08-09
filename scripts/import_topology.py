#!/usr/bin/env python3
"""
Step 3 of ../README.md — import ../topology.json as a new Air simulation
and start it, now that the cdrom image it references (sno-discovery-iso)
already exists (see upload_discovery_iso.py).

Run:
    python import_topology.py
"""
from __future__ import annotations

from pathlib import Path

from upload_discovery_iso import get_api  # reuse existing auth config

TOPOLOGY_PATH = Path(__file__).resolve().parent.parent / "topology.json"


def main() -> None:
    api = get_api()

    print(f"Importing {TOPOLOGY_PATH} and starting the simulation ...")
    sim = api.simulations.import_from_simulation_manifest(
        simulation_manifest=TOPOLOGY_PATH,
        attempt_start=True,
    )
    print(f"Simulation created: id={sim.id} name={sim.name!r} state={sim.state!r}")
    print(
        "Watch it boot from the node console in the Air UI, then continue "
        "with Step 4 (Assisted Installer host discovery)."
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Step 3 of ../README.md — create the Air simulation from ../topology.json
and start it.

This is the step that brings the whole lab up: it creates the
`sno-cluster` node itself, and — because topology.json omits an explicit
`"oob"` key (which defaults to on) and wires no other link to `eth0` —
Air automatically provisions two extra nodes alongside it:

    - oob-mgmt-switch-leaf-1   (the virtual switch for the OOB network)
    - oob-mgmt-server          (DHCP/DNS/NAT gateway for 192.168.200.0/24)

You don't define those two in topology.json; they're implicit
infrastructure Air creates for you the moment a node's `eth0` is left on
the default OOB network. This script just imports the manifest and starts
it — nothing more.

Prerequisite: Air images referenced by topology.json must already exist:
"cdrom" dsxair-discovery-iso (upload_discovery_iso.py) and "os"
blank-100g (upload_blank_disk.py).

This also sets up the SSH jump host onto oob-mgmt-server (see
04_create_jump_host_service.py) so you walk away from this one script with
both the node booting from the discovery ISO *and* a working way to reach
its private OOB address afterward.

Run:
    python 01_create_simulation.py
"""
from __future__ import annotations

from pathlib import Path

from air_common import (
    ensure_jump_host_service,
    get_api,
    jump_host_ssh_command,
    wait_for_sim_state,
)

TOPOLOGY_PATH = Path(__file__).resolve().parent.parent / "topology.json"


def _print_jump_host(sim) -> None:
    print("\nSetting up the oob-mgmt-server jump host ...")
    try:
        service, server = ensure_jump_host_service(sim)
    except SystemExit as exc:
        print(f"  skipped: {exc} (run 04_create_jump_host_service.py once the "
              "simulation is ACTIVE to set this up later)")
        return
    print(f"Jump host ready: {server.name!r} reachable via:\n\n    {jump_host_ssh_command(service, server)}\n")


def main() -> None:
    api = get_api()

    existing = [s for s in api.simulations.list(search="sno-cluster") if s.name == "sno-cluster"]
    if existing:
        sim = existing[0]
        print(f"Simulation 'sno-cluster' already exists (id={sim.id}, state={sim.state!r}).")
        print("Delete it first in the Air UI if you want a truly fresh start, "
              "or use 02_attach_discovery_iso.py / 03_boot_to_disk.py to manage it as-is.")
        _print_jump_host(sim)
        return

    print(f"Importing {TOPOLOGY_PATH} and starting the simulation ...")
    sim = api.simulations.import_from_simulation_manifest(
        simulation_manifest=TOPOLOGY_PATH,
        attempt_start=True,
    )
    print(f"Simulation created: id={sim.id} name={sim.name!r} state={sim.state!r}")

    wait_for_sim_state(sim, "ACTIVE", timeout=120)

    print("\nNodes in the simulation:")
    for node in sim.nodes.list():
        print(f"  - {node.name} (state={node.state})")

    _print_jump_host(sim)

    print(
        "\nWatch sno-cluster boot from the node console in the Air UI, then "
        "continue with Step 4 in ../README.md (Assisted Installer host discovery)."
    )


if __name__ == "__main__":
    main()

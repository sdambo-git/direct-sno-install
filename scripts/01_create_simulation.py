#!/usr/bin/env python3
"""
Step 3 of ../README.md — create the Air simulation from the topology manifest
and start it.

This is the step that brings the whole lab up: it creates the OCP node(s) from
topology.json (or topology-multinode.json), and — because the manifest omits an
explicit `"oob"` key (which defaults to on) and wires no other link to `eth0` —
Air automatically provisions two extra nodes alongside:

    - oob-mgmt-switch-leaf-1   (the virtual switch for the OOB network)
    - oob-mgmt-server          (DHCP/DNS/NAT gateway for 192.168.200.0/24)

Prerequisite: Air images referenced by the topology must already exist.

Run:
    python 01_create_simulation.py
"""
from __future__ import annotations

import sys

from air_common import (
    ensure_jump_host_ready,
    get_api,
    jump_host_ssh_command,
    wait_for_sim_state,
)
import env_config


def _print_jump_host(sim) -> None:
    print("\nSetting up the oob-mgmt-server jump host ...")
    try:
        service, server = ensure_jump_host_ready(sim)
    except SystemExit as exc:
        print(f"  skipped: {exc} (run bootstrap_jump_host.py once the "
              "simulation is ACTIVE to set this up later)")
        return
    print(f"Jump host ready: {server.name!r} reachable via:\n\n    {jump_host_ssh_command(service, server)}\n")


def main() -> None:
    api = get_api()
    topology_path = env_config.topology_path()
    sim_name = env_config.simulation_name()
    node_names = env_config.topology_node_names()

    existing = [s for s in api.simulations.list(search=sim_name) if s.name == sim_name]
    if existing:
        sim = existing[0]
        print(f"Simulation {sim_name!r} already exists (id={sim.id}, state={sim.state!r}).")
        print("Delete it first in the Air UI if you want a truly fresh start, "
              "or use 02_attach_discovery_iso.py / 09_recover_to_discovery.py to manage it.")
        _print_jump_host(sim)
        return

    print(f"Importing {topology_path} and starting simulation {sim_name!r} ...")
    sim = api.simulations.import_from_simulation_manifest(
        simulation_manifest=topology_path,
        attempt_start=True,
    )
    print(f"Simulation created: id={sim.id} name={sim.name!r} state={sim.state!r}")

    wait_for_sim_state(sim, "ACTIVE", timeout=120)

    print("\nNodes in the simulation:")
    for node in sim.nodes.list():
        print(f"  - {node.name} (state={node.state})")

    _print_jump_host(sim)

    nodes_hint = ", ".join(node_names) if node_names else sim_name
    print(
        f"\nWatch {nodes_hint} boot from the discovery ISO in the Air UI, then "
        "continue with 06_wait_for_host_ipv4.py (Assisted Installer host discovery)."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

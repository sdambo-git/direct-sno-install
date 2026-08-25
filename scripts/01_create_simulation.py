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

If a simulation with this name already exists, its nodes' attached cdrom
images are compared against topology.json. A stale one (e.g. left over from
before a fresh discovery-ISO upload under a new name) is deleted and
re-imported automatically. Pass --force to always recreate, even when it
looks aligned.

Run:
    python 01_create_simulation.py
    python 01_create_simulation.py --force
"""
from __future__ import annotations

import argparse
import sys

from air_common import (
    ensure_jump_host_ready,
    get_api,
    jump_host_ssh_command,
    wait_for_sim_state,
)
import env_config


def _find_image_by_name(api, name: str):
    return next(
        (img for img in api.images.list(search=name) if img.name == name),
        None,
    )


def _node_cdrom_image_ref(node) -> tuple[str | None, str | None]:
    """Best-effort (name, id) of the image currently in a node's cdrom drive."""
    cdrom = node.cdrom if isinstance(node.cdrom, dict) else {}
    image = cdrom.get("image")
    if isinstance(image, dict):
        return image.get("name"), image.get("id")
    if isinstance(image, str):
        return None, image
    return None, None


def _stale_cdrom_nodes(api, sim, node_names: list[str]) -> list[tuple[str, str, str]]:
    """Topology nodes whose live cdrom attachment doesn't match topology.json.

    Returns (node_name, expected_image_name, actual_description) triples.
    Best-effort: any SDK/API surprise is treated as "can't tell" (no
    mismatch reported) rather than blocking reuse of the simulation.
    """
    stale: list[tuple[str, str, str]] = []
    try:
        nodes_by_name = {n.name: n for n in sim.nodes.list()}
    except Exception:  # noqa: BLE001
        return stale
    for node_name in node_names:
        expected_name = env_config.node_cdrom_image(node_name)
        node = nodes_by_name.get(node_name)
        if node is None:
            stale.append((node_name, expected_name, "node missing from simulation"))
            continue
        try:
            actual_name, actual_id = _node_cdrom_image_ref(node)
        except Exception:  # noqa: BLE001
            continue
        if actual_name == expected_name:
            continue
        expected_image = _find_image_by_name(api, expected_name)
        if expected_image is not None and actual_id == expected_image.id:
            continue
        stale.append((node_name, expected_name, actual_name or actual_id or "none"))
    return stale


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing simulation and re-import fresh, even if it "
        "looks aligned with the current topology.",
    )
    args = parser.parse_args()

    api = get_api()
    topology_path = env_config.topology_path()
    sim_name = env_config.simulation_name()
    node_names = env_config.topology_node_names()

    existing = [s for s in api.simulations.list(search=sim_name) if s.name == sim_name]
    if existing:
        sim = existing[0]
        print(f"Simulation {sim_name!r} already exists (id={sim.id}, state={sim.state!r}).")
        stale = _stale_cdrom_nodes(api, sim, node_names) if node_names else []
        if stale:
            print("Stale: its nodes' discovery ISO doesn't match the current topology:")
            for node_name, expected, actual in stale:
                print(f"  - {node_name}: attached={actual!r} expected={expected!r}")
        if stale or args.force:
            reason = "stale cdrom images" if stale else "--force"
            print(f"Deleting simulation {sim.id} ({reason}) to import a fresh one ...")
            sim.delete()
        else:
            print("Delete it first in the Air UI if you want a truly fresh start, "
                  "or use 09_recover_to_discovery.py to manage it.")
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

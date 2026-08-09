#!/usr/bin/env python3
"""
Shared helpers used by the numbered scripts in this directory
(01_create_simulation.py, 02_attach_discovery_iso.py,
03_boot_to_disk.py). Not meant to be run directly.

Handles two annoying, empirically-discovered Air quirks:

- Changing a node's `cdrom`/`advanced.boot` fields requires the simulation
  to be fully INACTIVE first (patching while ACTIVE is silently accepted
  for some fields but rejected or ignored for others).
- Air auto-creates a checkpoint on shutdown, and that checkpoint must
  reach the COMPLETE state before it (or the simulation) can be
  manipulated further, otherwise you get:
      "The checkpoint must be in the `COMPLETE` state."
"""
from __future__ import annotations

import time

from air_sdk import AirApi
from air_sdk.endpoints.nodes import Node
from air_sdk.endpoints.services import Service
from air_sdk.endpoints.simulations import Simulation

from upload_discovery_iso import get_api  # noqa: F401  (re-exported)

SIMULATION_NAME = "sno-cluster"
NODE_NAME = "sno-cluster"

# Air auto-provisions these two nodes whenever a node's eth0 is left on the
# default OOB network — they're never defined in topology.json.
OOB_SERVER_NAME = "oob-mgmt-server"
OOB_SERVER_INTERFACE = "eth0"
JUMP_HOST_SERVICE_NAME = "oob-mgmt-server SSH"


def get_simulation(api: AirApi, name: str = SIMULATION_NAME) -> Simulation:
    sims = list(api.simulations.list(search=name))
    matches = [s for s in sims if s.name == name]
    if not matches:
        raise SystemExit(
            f"No simulation named {name!r} found. Run 01_create_simulation.py first."
        )
    return matches[0]


def get_node(sim: Simulation, name: str = NODE_NAME) -> Node:
    for node in sim.nodes.list():
        if node.name == name:
            return node
    raise SystemExit(f"No node named {name!r} found in simulation {sim.name!r}.")


def wait_for_sim_state(sim: Simulation, *states: str, timeout: int = 180, interval: int = 4) -> None:
    deadline = time.monotonic() + timeout
    while True:
        sim.refresh()
        print(f"  simulation state: {sim.state}")
        if sim.state in states:
            return
        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {timeout}s waiting for simulation state in {states} "
                f"(last seen: {sim.state!r})."
            )
        time.sleep(interval)


def stop_simulation_and_clear_checkpoints(sim: Simulation) -> None:
    """Stop the simulation (if running) and delete any checkpoints so the
    node can be safely patched afterwards."""
    if sim.state != "INACTIVE":
        print(f"Stopping simulation {sim.name!r} ...")
        sim.shutdown()
        wait_for_sim_state(sim, "INACTIVE", timeout=240)
    else:
        print(f"Simulation {sim.name!r} is already INACTIVE.")

    checkpoints = list(sim.checkpoints.list())
    if not checkpoints:
        return

    print(f"Clearing {len(checkpoints)} checkpoint(s) before patching the node ...")
    deadline = time.monotonic() + 120
    for cp in checkpoints:
        while True:
            cp.refresh()
            state = getattr(cp, "state", None)
            if state == "COMPLETE":
                break
            if state == "DELETED":
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"Timed out waiting for checkpoint {cp.id} to become COMPLETE.")
            time.sleep(3)
        try:
            cp.delete()
            print(f"  deleted checkpoint {cp.id} ({cp.name})")
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            print(f"  warning: could not delete checkpoint {cp.id}: {exc}")


def start_simulation(sim: Simulation) -> None:
    print(f"Starting simulation {sim.name!r} ...")
    sim.start()
    wait_for_sim_state(sim, "ACTIVE", timeout=180)


def ensure_jump_host_service(
    sim: Simulation, service_name: str = JUMP_HOST_SERVICE_NAME
) -> tuple[Service, Node]:
    """Idempotently expose oob-mgmt-server's SSH port as an Air Service.

    Reuses an existing SSH service on that interface if one is already
    there (so re-running this doesn't create duplicates), otherwise
    creates one. Returns (service, oob_mgmt_server_node) so callers can
    build the ssh command and know which user to log in as.
    """
    server = get_node(sim, OOB_SERVER_NAME)

    iface = next(
        (i for i in server.interfaces.list() if i.name == OOB_SERVER_INTERFACE), None
    )
    if iface is None:
        raise SystemExit(
            f"No {OOB_SERVER_INTERFACE!r} interface found on node {OOB_SERVER_NAME!r}."
        )

    existing = next((svc for svc in iface.services.list() if svc.node_port == 22), None)
    if existing is not None:
        return existing, server

    service = sim.create_service(
        name=service_name,
        interface=iface,
        dest_port=22,
        service_type="SSH",
    )
    return service, server


def jump_host_ssh_command(service: Service, server: Node) -> str:
    username = getattr(server.image, "default_username", None) or "ubuntu"
    return f"ssh -p {service.worker_port} {username}@{service.worker_fqdn}"

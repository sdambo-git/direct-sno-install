#!/usr/bin/env python3
"""
Add a new utility/jump host to the existing `sno-cluster` simulation, wired
onto the same OOB management network as `sno-cluster` itself.

Unlike 01_create_simulation.py (which imports a whole topology manifest and
only works for a fresh simulation), this uses `api.nodes.create(...)`
directly against the existing `sno-cluster` simulation — no need to
delete/recreate anything.

Connectivity to sno-cluster comes for free: `sno-cluster`'s auto_oob_enabled
is True (confirmed via `simulation.auto_oob_enabled`), so any new node
created here without explicit interface/link wiring is automatically
attached to the same auto-provisioned OOB switch (oob-mgmt-switch-leaf-1)
and NAT gateway (oob-mgmt-server) as sno-cluster, landing on the same
192.168.200.0/24 subnet.

IMPORTANT: the Air API rejects node creation on an ACTIVE simulation
("The simulation must be in the INACTIVE state to perform this
operation.") — same quirk as the cdrom/boot patching issue documented in
air_common.py. This script therefore stops the whole simulation (which
also stops the already-installed sno-cluster OpenShift node!) before
creating the node, then starts it back up. Expect a brief outage of the
running cluster while this runs.

Run:
    python host-creation.py
"""
from __future__ import annotations

from air_common import (
    get_api,
    get_simulation,
    start_simulation,
    stop_simulation_and_clear_checkpoints,
)

NEW_NODE_NAME = "utility-host"
IMAGE_NAME = "centos9"
CPU = 4
MEMORY_MIB = 8192
STORAGE_GIB = 50
ADVANCED = {
    "cpu_mode": "host-passthrough",
    "nic_model": "virtio",
    "uefi": False,
    "secureboot": False,
}


def main() -> None:
    api = get_api()
    sim = get_simulation(api)

    existing = [n for n in sim.nodes.list() if n.name == NEW_NODE_NAME]
    if existing:
        node = existing[0]
        print(f"Node {NEW_NODE_NAME!r} already exists (id={node.id}, state={node.state}).")
    else:
        images = [i for i in api.images.list(search=IMAGE_NAME) if i.name == IMAGE_NAME]
        if not images:
            raise SystemExit(f"No catalog image named {IMAGE_NAME!r} found.")
        image = images[0]

        stop_simulation_and_clear_checkpoints(sim)

        print(f"Creating node {NEW_NODE_NAME!r} ({IMAGE_NAME}, {CPU} vCPU / "
              f"{MEMORY_MIB}MiB / {STORAGE_GIB}GiB) in simulation {sim.name!r} ...")
        node = api.nodes.create(
            name=NEW_NODE_NAME,
            simulation=sim,
            image=image,
            cpu=CPU,
            memory=MEMORY_MIB,
            storage=STORAGE_GIB,
            advanced=ADVANCED,
        )
        print(f"Created: id={node.id} state={node.state}")

        start_simulation(sim)

    print("\nInterfaces:")
    for iface in node.interfaces.list():
        print(f"  {iface.name}: connection={getattr(iface, 'connection', None)}")

    print(
        f"\nBoot {NEW_NODE_NAME!r} from the Air console, then check its DHCP-assigned "
        "address on the OOB network the same way direct-sno-install's README does "
        "(Host discovery table, or SSH via the oob-mgmt-server jump host). It should "
        "be able to reach sno-cluster directly over 192.168.200.0/24."
    )


if __name__ == "__main__":
    main()

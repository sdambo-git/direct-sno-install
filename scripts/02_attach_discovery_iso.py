#!/usr/bin/env python3
"""
Attach (or re-attach) the Assisted Installer discovery ISO to sno-cluster
and boot from it.

Use this:
  - The first time, right after 01_create_simulation.py, if topology.json
    didn't already have the cdrom wired up.
  - Any time you need the host to re-run discovery — e.g. after clicking
    "Reset Cluster" in the Assisted Installer console following a failed
    install, so the discovery agent exists again to re-register the host.

What it does, in order (the sequence matters — Air rejects/ignores
`cdrom`/boot-order changes on a running node):
  1. Stops the simulation (node edits are rejected/ignored while ACTIVE).
  2. Clears any auto-generated checkpoints left over from the stop.
  3. Sets the node's cdrom to the sno-discovery-iso image and boot order
     to cdrom-first.
  4. Restarts the simulation, so it boots straight into the discovery ISO.

Run:
    python 02_attach_discovery_iso.py
"""
from __future__ import annotations

from air_common import (
    get_api,
    get_node,
    get_simulation,
    start_simulation,
    stop_simulation_and_clear_checkpoints,
)
from upload_discovery_iso import IMAGE_NAME


def main() -> None:
    api = get_api()
    sim = get_simulation(api)
    node = get_node(sim)

    images = [img for img in api.images.list(search=IMAGE_NAME) if img.name == IMAGE_NAME]
    if not images:
        raise SystemExit(
            f"Image {IMAGE_NAME!r} not found in Air. Run upload_discovery_iso.py first."
        )
    image = images[0]

    print(f"Current state: node.cdrom={node.cdrom!r} advanced.boot={node.advanced.get('boot')!r}")

    stop_simulation_and_clear_checkpoints(sim)

    print(f"Attaching cdrom image {IMAGE_NAME!r} ({image.id}) ...")
    # Merge into existing advanced — a partial advanced= update can reset
    # cpu_mode (e.g. host-passthrough → custom) and break the guest.
    advanced = dict(node.advanced or {})
    advanced["boot"] = ["hd", "cdrom"]
    if not advanced.get("cpu_mode"):
        advanced["cpu_mode"] = "host-passthrough"
    print(f"Setting cdrom + advanced boot={advanced.get('boot')!r} "
          f"cpu_mode={advanced.get('cpu_mode')!r} ...")
    node.update(cdrom={"image": image.id}, advanced=advanced)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")
    print(f"  advanced now: {node.advanced}")

    start_simulation(sim)

    print(
        "\nsno-cluster is booting from the discovery ISO. Watch the node "
        "console in the Air UI, and check the Assisted Installer console "
        "for the host to re-register under Host discovery within a couple "
        "of minutes."
    )


if __name__ == "__main__":
    main()

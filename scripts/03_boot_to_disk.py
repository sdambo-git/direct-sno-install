#!/usr/bin/env python3
"""
Switch sno-cluster's boot order from cdrom to disk, and detach the
discovery ISO.

Run this ONCE, right after Assisted Installer's progress page shows the
"Writing image to disk: 100%" / "Rebooting" event for the host — i.e.
*before* its first post-install reboot actually happens.

Why this matters (the 35% stall): with
`boot: ["cdrom", "hd"]` still in place, that first reboot boots the live
discovery ISO again instead of the freshly-installed disk. The discovery
agent then reports "Expected the host to boot from disk, but it booted
the installation image", and Assisted Installer parks the whole cluster
in `installing-pending-user-action` — a dead end that requires Abort +
Reset Cluster to recover from. Run this script before that reboot happens
and you avoid the whole detour.

If you're recovering from that exact stuck state already: Abort the
install, click Reset Cluster in the console, then run
02_attach_discovery_iso.py instead to re-run discovery from scratch —
running this script won't help at that point, since there's no active
install to protect.

What it does:
  1. Stops the simulation.
  2. Clears any auto-generated checkpoints.
  3. Sets the node's boot order to hd-only and detaches the cdrom
     (cdrom=None).
  4. Restarts the simulation, so the pending reboot lands on disk.

Run:
    python 03_boot_to_disk.py
"""
from __future__ import annotations

from air_common import (
    get_api,
    get_node,
    get_simulation,
    start_simulation,
    stop_simulation_and_clear_checkpoints,
)


def main() -> None:
    api = get_api()
    sim = get_simulation(api)
    node = get_node(sim)

    print(f"Current state: node.cdrom={node.cdrom!r} advanced.boot={node.advanced.get('boot')!r}")

    stop_simulation_and_clear_checkpoints(sim)

    print("Setting boot order to hd-only ...")
    node.update(advanced={"boot": "hd"})
    node.refresh()
    print(f"  advanced now: {node.advanced}")

    print("Detaching cdrom ...")
    node.update(cdrom=None)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")

    start_simulation(sim)

    print(
        "\nsno-cluster will now boot from disk on its next reboot. If the "
        "install was already mid-reboot-loop when you ran this, give it a "
        "couple of minutes and check Assisted Installer's progress page — "
        "it should resume past 'Rebooting' instead of stalling."
    )


if __name__ == "__main__":
    main()

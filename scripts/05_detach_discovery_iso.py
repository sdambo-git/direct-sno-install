#!/usr/bin/env python3
"""
Detach the discovery ISO from sno-cluster once the install is complete and
you no longer want the cdrom sitting there.

Run this any time after Assisted Installer's install has finished and
`sno-cluster` is reliably booting from disk on its own (i.e. you don't
need cdrom as a `hd`-failover target anymore).

Why this needs a boot-order change too (not just `cdrom=None`): Air
enforces "a CD-ROM must be attached whenever the node's boot order
includes cdrom" on live patches, not just at import time. Confirmed
empirically:

    node.update(cdrom=None)   # boot order still ["hd", "cdrom"]
    -> 400: {"advanced": {"boot": {"message": "Boot device 'cdrom'
       requires a CD-ROM image attached to the node via the `cdrom`
       field.", "code": "invalid"}}}

So this script drops `cdrom` from the boot order (down to `hd`-only) in
the same patch that detaches the image. This is a one-way, final-cleanup
operation, not the toggle-it-back-and-forth anti-pattern described in
../README.md ("boot order stays ["hd", "cdrom"] — don't toggle it") — once
you run this, the node can no longer fail over to cdrom. If you need a
discovery boot again later, re-attach a cdrom image and restore
`boot: ["hd", "cdrom"]` (see scripts/02_attach_discovery_iso.py), or
rebuild the node back to a blank disk (see the README's rebuild note).

What it does, in order (same stop -> patch -> restart sequence as the
other scripts, since Air rejects/ignores cdrom/boot-order changes on a
running node):
  1. Stops the simulation and clears any leftover checkpoints.
  2. Sets boot order to hd-only.
  3. Detaches the cdrom (cdrom=None).
  4. Restarts the simulation.

Run:
    python 05_detach_discovery_iso.py
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

    if node.cdrom is None:
        print("No cdrom attached — nothing to do.")
        return

    stop_simulation_and_clear_checkpoints(sim)

    print("Setting boot order to hd-only (required before detaching cdrom) ...")
    node.update(advanced={"boot": "hd"})
    node.refresh()
    print(f"  advanced now: {node.advanced}")

    print("Detaching cdrom ...")
    node.update(cdrom=None)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")

    start_simulation(sim)

    print(
        "\nsno-cluster no longer has a cdrom attached and boots hd-only. "
        "If you need discovery mode again later, see "
        "scripts/02_attach_discovery_iso.py or the README's rebuild note."
    )


if __name__ == "__main__":
    main()

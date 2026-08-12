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

07_install_cluster.py runs this automatically by default; use this script
only for manual recovery or when --no-auto-boot-to-disk was used.

Run:
    uv run 03_boot_to_disk.py
"""
from __future__ import annotations

from air_common import boot_node_to_disk, get_api, get_simulation


def main() -> None:
    api = get_api()
    sim = get_simulation(api)
    boot_node_to_disk(sim)
    print(
        "\nsno-cluster will now boot from disk on its next reboot. If the "
        "install was already mid-reboot-loop when you ran this, give it a "
        "couple of minutes and check Assisted Installer's progress page — "
        "it should resume past 'Rebooting' instead of stalling."
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Create (or reuse) an SSH service exposing oob-mgmt-server's eth0 — the jump
host onto sno-cluster's private OOB network (192.168.200.0/24).

sno-cluster's own address isn't reachable directly from outside Air (see
../README.md, Step 5), so this is how you get to it: `oob-mgmt-server` sits
on the same private subnet, and Air can expose its SSH port on a public
worker FQDN/port pair. From there you can either run `oc`/`ssh` straight
from oob-mgmt-server, or add an SSH local port-forward to reach the
cluster's API from your own laptop.

Safe to run any time after 01_create_simulation.py has created the
simulation — oob-mgmt-server is auto-provisioned alongside sno-cluster the
moment the node's eth0 is left on the default OOB network. Re-running this
reuses the existing service instead of creating a duplicate.

Run:
    python 04_create_jump_host_service.py
"""
from __future__ import annotations

from air_common import ensure_jump_host_ready, get_api, get_simulation, jump_host_ssh_command


def main() -> None:
    api = get_api()
    sim = get_simulation(api)

    service, server = ensure_jump_host_ready(sim)
    ssh_command = jump_host_ssh_command(service, server)

    print(f"Jump host ready: {server.name!r} reachable via:\n\n    {ssh_command}\n")
    print(
        "From there:\n"
        "  - Run `oc`/`ssh` directly on oob-mgmt-server — it's on the same "
        "192.168.200.0/24 network as sno-cluster, so it reaches it directly.\n"
        "  - Or tunnel the cluster's API port back to your laptop by adding "
        "`-L 6443:<sno-cluster-ip>:6443` to the ssh command above (get "
        "sno-cluster's IP from Assisted Installer's Host discovery table or "
        "the Air node console)."
    )


if __name__ == "__main__":
    main()

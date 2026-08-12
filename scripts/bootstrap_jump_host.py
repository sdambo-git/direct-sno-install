#!/usr/bin/env python3
"""
Bootstrap oob-mgmt-server for non-interactive SSH.

NVIDIA Air's auto-provisioned oob-mgmt-server image forces a password change
on first login (default user ``ubuntu``, factory password ``nvidia``). Until
that happens, pubkey SSH connects but every command fails with::

    Password change required but no TTY available.

This script exposes the jump-host SSH service (if needed) and performs the
one-time password change automatically. Safe to re-run — it no-ops when the
host already accepts BatchMode SSH.

Environment (optional):
    JUMP_HOST_INITIAL_PASSWORD   factory password (default: image default or ``nvidia``)
    JUMP_HOST_PASSWORD           new password (default: ``redhat``)

Run:
    uv run bootstrap_jump_host.py
"""
from __future__ import annotations

import argparse

from air_common import (
    ensure_jump_host_ready,
    ensure_jump_host_service,
    get_api,
    get_simulation,
    jump_host_ssh_command,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service-only",
        action="store_true",
        help="Only create/reuse the SSH service; do not change the password.",
    )
    args = parser.parse_args()

    api = get_api()
    sim = get_simulation(api)

    if args.service_only:
        service, server = ensure_jump_host_service(sim)
    else:
        service, server = ensure_jump_host_ready(sim)

    print(
        f"Jump host ready: {server.name!r} reachable via:\n\n"
        f"    {jump_host_ssh_command(service, server)}\n"
    )


if __name__ == "__main__":
    main()

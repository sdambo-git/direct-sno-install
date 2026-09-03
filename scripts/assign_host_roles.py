#!/usr/bin/env python3
"""
Assign Assisted Installer host roles (master/worker) based on topology node names.

Run after 06_wait_for_host_ipv4.py when hosts are known/ready and before
07_install_cluster.py for multinode profiles. Idempotent: skips hosts that
already have the desired role (including already-installed hosts).

    CLUSTER_PROFILE=multinode uv run assign_host_roles.py
    uv run assign_host_roles.py --dry-run
"""
from __future__ import annotations

import argparse
import sys

from assisted_common import assign_topology_host_roles, get_client
import env_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned role assignments without calling the API.",
    )
    args = parser.parse_args()

    if not env_config.is_multinode():
        print("CLUSTER_PROFILE is not multinode — role assignment is a no-op for SNO.")
        return

    ai = get_client(quiet=False)
    assign_topology_host_roles(ai, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

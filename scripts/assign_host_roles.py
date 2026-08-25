#!/usr/bin/env python3
"""
Assign Assisted Installer host roles (master/worker) based on topology node names.

Deploy / 06_wait_for_host_ipv4.py pins roles as soon as a host hostname matches
ocp-cp-* or ocp-worker-*. This script is the same mapping, requiring every
topology node to be present (use after discovery).

    CLUSTER_PROFILE=multinode uv run assign_host_roles.py
    uv run assign_host_roles.py --dry-run
"""
from __future__ import annotations

import argparse
import sys

from assisted_common import assign_topology_roles, get_client, hosts_by_topology_name
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
    mapping = hosts_by_topology_name(ai)
    topo_names = [n for n in env_config.topology_node_names() if n.startswith("ocp-")]
    if not topo_names:
        topo_names = env_config.control_plane_node_names() + [
            n for n in env_config.worker_node_names() if "mgmt" not in n.lower()
        ]
    missing = [name for name in topo_names if name not in mapping]
    if missing:
        raise SystemExit(
            f"Could not match all topology nodes to AI hosts. Missing: {missing}. "
            f"Matched: {list(mapping.keys())}. "
            "Check host discovery or set requested_hostname in Assisted Installer."
        )

    changed = assign_topology_roles(ai, dry_run=args.dry_run)
    if args.dry_run:
        print("Dry run only; no changes applied." if changed else "Roles already match topology.")
    elif not changed:
        print("Host roles already match topology.")
    else:
        print("Host roles updated.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

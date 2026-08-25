#!/usr/bin/env python3
"""
Assign Assisted Installer host roles (master/worker) based on topology node names.

Run after 06_wait_for_host_ipv4.py when hosts are known/ready and before
07_install_cluster.py for multinode profiles.

    CLUSTER_PROFILE=multinode uv run assign_host_roles.py
    uv run assign_host_roles.py --dry-run
"""
from __future__ import annotations

import argparse
import sys

from assisted_common import get_client, hosts_by_topology_name
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
    topo_names = env_config.topology_node_names()
    if len(mapping) < len(topo_names):
        missing = [name for name in topo_names if name not in mapping]
        raise SystemExit(
            f"Could not match all topology nodes to AI hosts. Missing: {missing}. "
            f"Matched: {list(mapping.keys())}. "
            "Check host discovery or set requested_hostname in Assisted Installer."
        )

    for topo_name, host in mapping.items():
        role = env_config.host_role_for_topology_node(topo_name)
        ai_hostname = host.get("requested_hostname") or host.get("id")
        current_role = host.get("role")
        print(
            f"  {topo_name!r} -> AI host {ai_hostname!r}: "
            f"role {current_role!r} -> {role!r}"
        )
        if args.dry_run:
            continue
        ai.update_host(ai_hostname, {"role": role})

    if args.dry_run:
        print("Dry run only; no changes applied.")
    else:
        print("Host roles updated.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

#!/usr/bin/env python3
"""
Wait until Assisted Installer reports a host for the lab cluster with a real
Air OOB IPv4 (192.168.200.0/24). Does not start the cluster install.

Requires AI_OFFLINETOKEN (or AI_OFFLINETOKEN_FILE). Run after the Air
simulation is ACTIVE and the node has booted the discovery ISO.

    uv run 06_wait_for_host_ipv4.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from ailib import AssistedClient

import env_config


def _get_client() -> AssistedClient:
    return AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=True,
    )


def _host_oob_ipv4s(host: dict) -> list[str]:
    inventory_raw = host.get("inventory")
    if not inventory_raw:
        return []
    try:
        inventory = json.loads(inventory_raw) if isinstance(inventory_raw, str) else inventory_raw
    except (TypeError, json.JSONDecodeError):
        return []
    found: list[str] = []
    for nic in inventory.get("interfaces") or []:
        for addr in nic.get("ipv4_addresses") or []:
            ip = str(addr).split("/")[0]
            if ip.startswith(env_config.OOB_IPV4_PREFIX):
                found.append(ip)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Seconds to wait before failing (default: 900).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Polling interval in seconds (default: 15).",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    ai = _get_client()
    deadline = time.monotonic() + args.timeout
    last_summary = "no hosts yet"

    print(
        f"Waiting up to {args.timeout}s for a host on cluster {name!r} "
        f"with IPv4 in {env_config.OOB_IPV4_PREFIX}0/24 ..."
    )

    while True:
        hosts = list(ai.list_hosts())
        cluster_id = next(
            (c.get("id") for c in ai.list_clusters() if c.get("name") == name),
            None,
        )
        if cluster_id is not None:
            bound = [h for h in hosts if h.get("cluster_id") == cluster_id]
            if bound:
                hosts = bound

        summaries = []
        for host in hosts:
            hostname = host.get("requested_hostname") or host.get("id")
            status = host.get("status")
            ips = _host_oob_ipv4s(host)
            summaries.append(f"{hostname} status={status} oob={ips or '-'}")
            if ips:
                print(
                    f"Host discovery succeeded: {hostname} has OOB IPv4 "
                    f"{ips[0]} (status={status})."
                )
                print(
                    "Next: in Assisted Installer, select the machine network / "
                    "API+Ingress VIP, then install when validations are green."
                )
                return

        last_summary = "; ".join(summaries) if summaries else "no hosts yet"
        print(f"  still waiting ({last_summary})")
        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {args.timeout}s waiting for OOB IPv4 "
                f"(last seen: {last_summary})."
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

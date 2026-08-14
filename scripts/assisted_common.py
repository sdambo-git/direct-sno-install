#!/usr/bin/env python3
"""Shared Assisted Installer helpers for numbered scripts."""
from __future__ import annotations

import json

from ailib import AssistedClient

import env_config


def get_client(*, quiet: bool = True) -> AssistedClient:
    return AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=quiet,
    )


def host_oob_ipv4s(host: dict) -> list[str]:
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


def cluster_hosts(ai: AssistedClient, cluster_name: str | None = None) -> list[dict]:
    name = cluster_name or env_config.cluster_name()
    cluster_id = ai.get_cluster_id(name)
    return [h for h in ai.list_hosts() if h.get("cluster_id") == cluster_id]


def primary_oob_ipv4(ai: AssistedClient, cluster_name: str | None = None) -> str:
    hosts = cluster_hosts(ai, cluster_name)
    for host in hosts:
        ips = host_oob_ipv4s(host)
        if ips:
            return ips[0]
    raise SystemExit(
        "No host with an OOB IPv4 found. Run 06_wait_for_host_ipv4.py first."
    )


def _host_matches_topology(host: dict, topology_name: str) -> bool:
    requested = (host.get("requested_hostname") or "").lower()
    inventory_hostname = ""
    inventory_raw = host.get("inventory")
    if inventory_raw:
        try:
            inventory = (
                json.loads(inventory_raw) if isinstance(inventory_raw, str) else inventory_raw
            )
            inventory_hostname = (inventory.get("hostname") or "").lower()
        except (TypeError, json.JSONDecodeError):
            pass
    needle = topology_name.lower()
    return needle in requested or needle in inventory_hostname or requested.startswith(needle)


def host_for_topology_node(ai: AssistedClient, topology_name: str) -> dict | None:
    for host in cluster_hosts(ai):
        if _host_matches_topology(host, topology_name):
            return host
    return None


def hosts_by_topology_name(ai: AssistedClient) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    unmatched = list(cluster_hosts(ai))
    for topo_name in env_config.topology_node_names():
        match = next((h for h in unmatched if _host_matches_topology(h, topo_name)), None)
        if match is not None:
            mapping[topo_name] = match
            unmatched.remove(match)
    if unmatched and env_config.topology_node_names():
        for host, topo_name in zip(unmatched, env_config.topology_node_names()):
            if topo_name not in mapping:
                mapping[topo_name] = host
    return mapping


def all_hosts_oob_ready(
    ai: AssistedClient,
    *,
    min_hosts: int | None = None,
    require_known: bool = False,
) -> tuple[bool, list[dict]]:
    """Return (ready, hosts) when min_hosts have OOB IPs (and optionally known/ready)."""
    required = min_hosts if min_hosts is not None else env_config.expected_hosts()
    hosts = cluster_hosts(ai)
    ready_hosts: list[dict] = []
    for host in hosts:
        ips = host_oob_ipv4s(host)
        status = host.get("status")
        if not ips:
            continue
        if require_known and status not in {"known", "ready"}:
            continue
        ready_hosts.append(host)
    return len(ready_hosts) >= required, ready_hosts

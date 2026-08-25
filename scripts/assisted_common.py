#!/usr/bin/env python3
"""Shared Assisted Installer helpers for numbered scripts."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

from ailib import AssistedClient

import env_config

T = TypeVar("T")

# Hosts in these statuses have checked in; HA stays in pending-for-input until
# VIPs are set (step 9). insufficient is NTP/validation — not installable yet.
INSTALLABLE_HOST_STATUSES = frozenset({"known", "ready", "pending-for-input"})


def get_client(*, quiet: bool = True) -> AssistedClient:
    return AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=quiet,
    )


def refresh_ai_token(ai: AssistedClient) -> None:
    """Refresh the Assisted Installer SaaS token (ailib requires both args)."""
    ai.refresh_token(ai.token, ai.offlinetoken)


def ai_retry(ai: AssistedClient, fn: Callable[[], T]) -> T:
    """Run an AI API call, refreshing the token once on 401."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "401" not in text and "token is invalid" not in text.lower():
            raise
        refresh_ai_token(ai)
        return fn()


def get_cluster_dict(ai: AssistedClient, cluster_name: str | None = None) -> dict:
    """Fetch cluster (including hosts) with token refresh on 401."""
    name = cluster_name or env_config.cluster_name()

    def _fetch() -> dict:
        cluster_id = ai.get_cluster_id(name)
        return ai.client.v2_get_cluster(cluster_id=cluster_id).to_dict()

    return ai_retry(ai, _fetch)


def _as_host_dict(host) -> dict:
    if isinstance(host, dict):
        return host
    if hasattr(host, "to_dict"):
        return host.to_dict()
    return {"id": str(getattr(host, "id", host))}


def cluster_hosts(ai: AssistedClient, cluster_name: str | None = None) -> list[dict]:
    """Hosts bound to this cluster.

    Prefer the cluster payload (what the AI UI shows). Fall back to listing
    infraenv hosts and comparing cluster_id as strings — UUID vs str mismatches
    used to make this look like 'no hosts yet' while the UI showed three.
    """
    cluster = get_cluster_dict(ai, cluster_name)
    raw = cluster.get("hosts") or []
    if raw:
        return [_as_host_dict(h) for h in raw]

    cid = str(cluster.get("id") or "")
    listed = ai_retry(ai, ai.list_hosts)
    matched: list[dict] = []
    for host in listed:
        hd = _as_host_dict(host)
        if str(hd.get("cluster_id") or "") == cid:
            matched.append(hd)
    return matched


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
    hosts: list[dict] | None = None,
) -> tuple[bool, list[dict]]:
    """Return (ready, hosts) when min_hosts have OOB IPs (and optionally known/ready).

    ``pending-for-input`` counts when require_known is set — HA hosts sit there
    until API/Ingress VIPs are applied in 07_install_cluster.py --configure-only.
    """
    required = min_hosts if min_hosts is not None else env_config.expected_hosts()
    if hosts is None:
        hosts = cluster_hosts(ai)
    ready_hosts: list[dict] = []
    for host in hosts:
        ips = host_oob_ipv4s(host)
        status = host.get("status")
        if not ips:
            continue
        if require_known and status not in INSTALLABLE_HOST_STATUSES:
            continue
        ready_hosts.append(host)
    return len(ready_hosts) >= required, ready_hosts


def ensure_additional_ntp(ai: AssistedClient, cluster_name: str | None = None) -> bool:
    """Set additional_ntp_source if missing. Returns True if an update was sent."""
    name = cluster_name or env_config.cluster_name()
    wanted = env_config.additional_ntp_source()
    cluster = get_cluster_dict(ai, name)
    current = (cluster.get("additional_ntp_source") or "").strip()
    if current:
        return False
    print(f"Setting additional_ntp_source={wanted!r} on cluster {name!r} ...")
    ai_retry(ai, lambda: ai.update_cluster(name, {"additional_ntp_source": wanted}) or None)
    return True

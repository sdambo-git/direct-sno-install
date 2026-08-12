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

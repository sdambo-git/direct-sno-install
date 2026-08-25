#!/usr/bin/env python3
"""Shared Assisted Installer helpers for numbered scripts."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

from ailib import AssistedClient

import env_config

T = TypeVar("T")


def get_client(*, quiet: bool = True) -> AssistedClient:
    return AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=quiet,
    )


def is_unauthorized(exc: BaseException) -> bool:
    status = getattr(exc, "status", None)
    if status == 401:
        return True
    text = str(exc)
    return "401" in text or "Unauthorized" in text or "token is invalid" in text.lower()


def refresh_ai(ai: AssistedClient) -> None:
    """Refresh the SaaS access token in place (OCM tokens expire mid-poll)."""
    try:
        ai.refresh_token(ai.token, ai.offlinetoken)
        return
    except Exception:  # noqa: BLE001
        replacement = get_client(quiet=getattr(ai, "quiet", True))
        ai.token = replacement.token
        ai.offlinetoken = replacement.offlinetoken
        ai.config = replacement.config
        ai.api = replacement.api
        ai.client = replacement.client


def ai_call(ai: AssistedClient, fn: Callable[[], T]) -> T:
    """Invoke an Assisted Installer call, refreshing once on 401."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if not is_unauthorized(exc):
            raise
        refresh_ai(ai)
        try:
            return fn()
        except Exception as exc2:  # noqa: BLE001
            if not is_unauthorized(exc2):
                raise
            refresh_ai(ai)
            return fn()


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
    cluster_id = ai_call(ai, lambda: ai.get_cluster_id(name))
    hosts = ai_call(ai, ai.list_hosts)
    return [h for h in hosts if h.get("cluster_id") == cluster_id]


def primary_oob_ipv4(ai: AssistedClient, cluster_name: str | None = None) -> str:
    hosts = cluster_hosts(ai, cluster_name)
    for host in hosts:
        ips = host_oob_ipv4s(host)
        if ips:
            return ips[0]
    raise SystemExit(
        "No host with an OOB IPv4 found. Run 06_wait_for_host_ipv4.py first."
    )


def _host_identity_names(host: dict) -> list[str]:
    requested = (host.get("requested_hostname") or "").strip()
    inventory_hostname = ""
    inventory_raw = host.get("inventory")
    if inventory_raw:
        try:
            inventory = (
                json.loads(inventory_raw) if isinstance(inventory_raw, str) else inventory_raw
            )
            inventory_hostname = (inventory.get("hostname") or "").strip()
        except (TypeError, json.JSONDecodeError):
            pass
    return [n for n in (requested, inventory_hostname) if n]


def _short_hostname(name: str) -> str:
    return name.strip().lower().split(".", 1)[0]


def _host_matches_topology(host: dict, topology_name: str) -> bool:
    needle = topology_name.lower()
    return any(_short_hostname(name) == needle for name in _host_identity_names(host))


def topology_name_for_host(
    host: dict, topo_names: list[str] | None = None
) -> str | None:
    """Return the Air topology node name for an Assisted host, or None if unknown.

    Matches requested_hostname / inventory hostname only. Never maps leftover
    UUID hosts onto leftover topology names (Assisted auto-role uses arrival order).
    """
    names = list(topo_names if topo_names is not None else env_config.topology_node_names())
    names.sort(key=len, reverse=True)
    for topo_name in names:
        if _host_matches_topology(host, topo_name):
            return topo_name
    return None


def host_for_topology_node(ai: AssistedClient, topology_name: str) -> dict | None:
    for host in cluster_hosts(ai):
        if _host_matches_topology(host, topology_name):
            return host
    return None


def hosts_by_topology_name(ai: AssistedClient) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for host in cluster_hosts(ai):
        topo_name = topology_name_for_host(host)
        if topo_name and topo_name not in mapping:
            mapping[topo_name] = host
    return mapping


# Assisted only allows host_role updates in these states (else 500).
_ROLE_PIN_STATUSES = frozenset(
    {"discovering", "known", "disconnected", "insufficient", "pending-for-input"}
)


def assign_topology_roles(
    ai: AssistedClient,
    hosts: list[dict] | None = None,
    *,
    dry_run: bool = False,
) -> list[tuple[str, str, str]]:
    """Set Assisted master/worker from topology names (ocp-cp-* / ocp-worker-*).

    Updates by host UUID only (hostname lookup is account-wide and can hit an
    installed host on another cluster). Skips hosts with no id, no topology
    name match, or a status that cannot change role. Pin API errors are
    warnings — discovery must keep polling. Returns (topology_name, host_id, role)
    for hosts that were updated (or would be on dry-run).
    """
    if not env_config.is_multinode():
        return []
    if hosts is None:
        hosts = cluster_hosts(ai)
    changed: list[tuple[str, str, str]] = []
    for host in hosts:
        topo_name = topology_name_for_host(host)
        if not topo_name:
            continue
        host_id = host.get("id")
        if not host_id:
            continue
        status = (host.get("status") or "").lower()
        if status not in _ROLE_PIN_STATUSES:
            continue
        role = env_config.host_role_for_topology_node(topo_name)
        pinned = (host.get("role") or "").lower()
        if pinned == role:
            continue
        print(
            f"Pinning topology role: {topo_name} -> {role} "
            f"(host id={host_id}, was role={host.get('role')!r} status={status!r})"
        )
        if not dry_run:
            try:
                ai_call(ai, lambda i=host_id, r=role: ai.update_host(i, {"role": r}))
            except Exception as exc:  # noqa: BLE001
                print(f"warning: could not pin role on {topo_name} ({host_id}): {exc}")
                continue
        changed.append((topo_name, str(host_id), role))
    return changed


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

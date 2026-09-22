from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dsx_air._bootstrap import ensure_scripts_path, repo_root

ensure_scripts_path()

import air_common  # noqa: E402
import env_config  # noqa: E402
from dsx_air import tunnel  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402

if TYPE_CHECKING:
    from air_sdk.endpoints.services import Service
    from air_sdk.endpoints.simulations import Simulation


class AirLookupError(Exception):
    """Air API lookup failed (missing key, sim not found, etc.)."""


def simulation_info(*, require_api_key: bool = True) -> dict[str, str]:
    """Return simulation id, name, and state from Air API."""
    if require_api_key and not env_config.air_api_key_configured():
        raise AirLookupError(
            "No Air API key. Put it in ~/.config/dsx-air/air-api-key "
            "or export AIR_API_KEY / AIR_API_KEY_FILE."
        )

    try:
        api = get_api()
        sim = air_common.get_simulation(api)
    except SystemExit as exc:
        raise AirLookupError(str(exc)) from exc

    return {
        "name": sim.name,
        "id": sim.id,
        "state": sim.state,
    }


def jump_host_info(*, sim: Simulation | None = None) -> dict[str, str]:
    """Return jump host SSH target and readiness without mutating."""
    if sim is None:
        if not env_config.air_api_key_configured():
            raise AirLookupError(
                "No Air API key. Put it in ~/.config/dsx-air/air-api-key "
                "or export AIR_API_KEY / AIR_API_KEY_FILE."
            )
        api = get_api()
        sim = air_common.get_simulation(api)

    sim.refresh()
    if sim.state != "ACTIVE":
        return {
            "ready": "no",
            "reason": f"simulation {sim.name} state is {sim.state!r} (need ACTIVE)",
            "ssh": "",
            "host": "",
            "port": "",
            "username": "",
        }

    try:
        server = air_common.get_node(sim, air_common.OOB_SERVER_NAME)
        iface = next(
            (
                i
                for i in server.interfaces.list()
                if i.name == air_common.OOB_SERVER_INTERFACE
            ),
            None,
        )
        if iface is None:
            return {
                "ready": "no",
                "reason": f"no {air_common.OOB_SERVER_INTERFACE} on jump host",
                "ssh": "",
                "host": "",
                "port": "",
                "username": "",
            }
        service = next(
            (svc for svc in iface.services.list() if svc.node_port == 22),
            None,
        )
        if service is None:
            return {
                "ready": "no",
                "reason": "jump host SSH service not exposed (run dsx-air start)",
                "ssh": "",
                "host": "",
                "port": "",
                "username": "",
            }
    except SystemExit as exc:
        raise AirLookupError(str(exc)) from exc

    host, port, username = air_common.jump_host_ssh_target(service, server)
    target = tunnel.JumpTarget(host=host, port=port, username=username)
    ssh = target.ssh_command
    ready, reason = air_common.jump_host_ssh_probe(service, server, timeout=15)
    return {
        "ready": "yes" if ready else "no",
        "reason": reason if not ready else "ok",
        "ssh": ssh,
        "host": host,
        "port": str(port),
        "username": username,
    }


def jump_target_from_info(jump: dict[str, str]) -> tunnel.JumpTarget | None:
    host = jump.get("host", "").strip()
    port = jump.get("port", "").strip()
    username = jump.get("username", "").strip()
    if not host or not port or not username:
        return None
    return tunnel.JumpTarget(host=host, port=int(port), username=username)


def profile_info() -> dict[str, str]:
    forward = api_forward_ip()
    return {
        "profile": env_config.cluster_profile(),
        "cluster_name": env_config.cluster_name(),
        "simulation_name": env_config.simulation_name(),
        "api_vip": env_config.api_vip(),
        "api_forward": forward,
        "ingress_forward": ingress_forward_ip(api_forward=forward),
        "multinode": "yes" if env_config.is_multinode() else "no",
    }


def _api_forward_cache_path(cluster_name: str) -> Path:
    return repo_root() / ".cache" / f"api-forward.{cluster_name}"


def remember_api_forward(cluster_name: str, ip: str) -> None:
    path = _api_forward_cache_path(cluster_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ip.strip() + "\n")


def api_forward_ip(*, cluster_name: str | None = None) -> str:
    """IP the jump host should LocalForward for the kube-apiserver.

    HA uses the Assisted API VIP (default 192.168.200.10). SNO has no VIP;
    the API listens on the node's OOB address (often 192.168.200.2).

    Tunnel/console must not call Assisted Installer here: ailib token setup
    can fail with HTTP 400 and abort the SSH command print.
    """
    if env_config.is_multinode():
        return env_config.api_vip()
    override = os.environ.get("API_FORWARD", "").strip()
    if override:
        return override
    name = cluster_name or env_config.cluster_name()
    cached = _api_forward_cache_path(name)
    if cached.is_file():
        ip = cached.read_text().strip().split()[0]
        if ip:
            return ip
    return f"{env_config.OOB_IPV4_PREFIX}2"


def ingress_forward_ip(*, api_forward: str | None = None) -> str:
    """HA Ingress VIP, or the SNO node IP (API and apps share the node)."""
    forward = api_forward if api_forward is not None else api_forward_ip()
    if env_config.is_multinode():
        return env_config.ingress_vip()
    return forward

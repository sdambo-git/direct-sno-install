from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dsx_air._bootstrap import ensure_scripts_path

ensure_scripts_path()

import air_common  # noqa: E402
import env_config  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402

if TYPE_CHECKING:
    from air_sdk.endpoints.services import Service
    from air_sdk.endpoints.simulations import Simulation


class AirLookupError(Exception):
    """Air API lookup failed (missing key, sim not found, etc.)."""


def simulation_info(*, require_api_key: bool = True) -> dict[str, str]:
    """Return simulation id, name, and state from Air API."""
    if require_api_key and not _has_air_api_key():
        raise AirLookupError("Set AIR_API_KEY (required for Air simulation lookup).")

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
        if not _has_air_api_key():
            raise AirLookupError("Set AIR_API_KEY (required for jump host lookup).")
        api = get_api()
        sim = air_common.get_simulation(api)

    sim.refresh()
    if sim.state != "ACTIVE":
        return {
            "ready": "no",
            "reason": f"simulation state is {sim.state!r} (need ACTIVE)",
            "ssh": "",
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
            }
    except SystemExit as exc:
        raise AirLookupError(str(exc)) from exc

    ssh = air_common.jump_host_ssh_command(service, server)
    ready, reason = air_common.jump_host_ssh_probe(service, server, timeout=15)
    return {
        "ready": "yes" if ready else "no",
        "reason": reason if not ready else "ok",
        "ssh": ssh,
    }


def profile_info() -> dict[str, str]:
    return {
        "profile": env_config.cluster_profile(),
        "cluster_name": env_config.cluster_name(),
        "simulation_name": env_config.simulation_name(),
        "api_vip": env_config.api_vip(),
        "multinode": "yes" if env_config.is_multinode() else "no",
    }


def _has_air_api_key() -> bool:
    if os.environ.get("AIR_API_KEY", "").strip():
        return True
    file_path = os.environ.get("AIR_API_KEY_FILE", "").strip()
    return bool(file_path)

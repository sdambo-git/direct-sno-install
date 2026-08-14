#!/usr/bin/env python3
"""
Shared env / file resolution for Air and Assisted Installer scripts.

Resolution rules:
  - Small secrets: ENV or ENV_FILE (file contents, stripped)
  - Bulky inputs: PATH env vars pointing at files on disk
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SAAS_AI_URL = "https://api.openshift.com"
DEFAULT_CLUSTER_NAME = "sno-cluster"
DEFAULT_MULTINODE_CLUSTER_NAME = "ocp-cluster"
DEFAULT_BASE_DNS_DOMAIN = "dsx.air.local"
DEFAULT_DISCOVERY_ISO_NAME = "dsxair-discovery-iso"
DEFAULT_BLANK_IMAGE_NAME = "blank-100g"
DEFAULT_JUMP_HOST_INITIAL_PASSWORD = "nvidia"
DEFAULT_JUMP_HOST_PASSWORD = "redhat"
DEFAULT_API_VIP = "192.168.200.10"
DEFAULT_INGRESS_VIP = "192.168.200.11"
OOB_IPV4_PREFIX = "192.168.200."
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_file(path: Path, *, what: str) -> str:
    if not path.is_file():
        raise SystemExit(f"{what} file not found: {path}")
    return path.read_text().strip()


def resolve_secret(env_name: str, *, what: str) -> str:
    """Resolve ENV or ENV_FILE into a non-empty secret string."""
    value = os.environ.get(env_name)
    if value:
        return value.strip()
    file_var = f"{env_name}_FILE"
    file_path = os.environ.get(file_var)
    if file_path:
        return _read_file(Path(file_path).expanduser(), what=what)
    raise SystemExit(
        f"No {what} set. Export {env_name}=... or {file_var}=/path/to/file."
    )


def air_api_key() -> str:
    return resolve_secret("AIR_API_KEY", what="Air API key")


def ai_offlinetoken() -> str:
    return resolve_secret("AI_OFFLINETOKEN", what="Assisted Installer offline token")


def pull_secret_path() -> Path:
    raw = os.environ.get("PULL_SECRET_PATH")
    if not raw:
        raise SystemExit(
            "No pull secret path set. Export PULL_SECRET_PATH=/path/to/pull-secret."
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise SystemExit(f"Pull secret file not found: {path}")
    return path


def ssh_public_key_path() -> Path:
    raw = os.environ.get("SSH_PUBLIC_KEY_PATH")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_file():
            raise SystemExit(f"SSH public key file not found: {path}")
        return path
    for candidate in (
        Path.home() / ".ssh" / "id_ed25519.pub",
        Path.home() / ".ssh" / "id_rsa.pub",
    ):
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "No SSH public key found. Export SSH_PUBLIC_KEY_PATH=/path/to/key.pub "
        "or place one at ~/.ssh/id_ed25519.pub or ~/.ssh/id_rsa.pub."
    )


def ssh_public_key() -> str:
    return _read_file(ssh_public_key_path(), what="SSH public key")


def ocp_version() -> str:
    value = os.environ.get("OCP_VERSION", "").strip()
    if not value:
        raise SystemExit(
            "No OpenShift version set. Export OCP_VERSION=4.19 (or similar)."
        )
    return value


def cluster_profile() -> str:
    return (os.environ.get("CLUSTER_PROFILE", "sno").strip() or "sno").lower()


def is_multinode() -> bool:
    return cluster_profile() == "multinode"


def cluster_name() -> str:
    if raw := os.environ.get("CLUSTER_NAME", "").strip():
        return raw
    return DEFAULT_MULTINODE_CLUSTER_NAME if is_multinode() else DEFAULT_CLUSTER_NAME


def topology_path() -> Path:
    raw = os.environ.get("TOPOLOGY_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_REPO_ROOT / path).resolve()
        return path
    default = "topology-multinode.json" if is_multinode() else "topology.json"
    return _REPO_ROOT / default


def simulation_name() -> str:
    return _load_topology_manifest().get("name") or cluster_name()


def expected_hosts() -> int:
    raw = os.environ.get("EXPECTED_HOSTS", "").strip()
    if raw:
        return int(raw)
    return len(topology_node_names()) or 1


def api_vip() -> str:
    return os.environ.get("API_VIP", DEFAULT_API_VIP).strip() or DEFAULT_API_VIP


def ingress_vip() -> str:
    return (
        os.environ.get("INGRESS_VIP", DEFAULT_INGRESS_VIP).strip()
        or DEFAULT_INGRESS_VIP
    )


def _load_topology_manifest() -> dict:
    path = topology_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def topology_node_names() -> list[str]:
    """OCP node names from topology manifest (excludes implicit oob-mgmt nodes)."""
    nodes = _load_topology_manifest().get("content", {}).get("nodes", {})
    return list(nodes.keys())


def _is_control_plane_topology_node(node_name: str) -> bool:
    lowered = node_name.lower()
    if any(token in lowered for token in ("worker", "compute")):
        return False
    return any(token in lowered for token in ("cp", "master", "control"))


def control_plane_node_names() -> list[str]:
    """Topology node names that map to Assisted Installer master role."""
    explicit = os.environ.get("CONTROL_PLANE_NODES", "").strip()
    if explicit:
        return [name.strip() for name in explicit.split(",") if name.strip()]
    names = topology_node_names()
    cp_names = [name for name in names if _is_control_plane_topology_node(name)]
    if cp_names:
        return cp_names
    return names[:1] if names else [cluster_name()]


def control_plane_count() -> int:
    raw = os.environ.get("CONTROL_PLANE_COUNT", "").strip()
    if raw:
        return int(raw)
    return len(control_plane_node_names())


def control_plane_node_name() -> str:
    """Primary control-plane node (first CP in topology order)."""
    explicit = os.environ.get("CONTROL_PLANE_NODE", "").strip()
    if explicit:
        return explicit
    names = control_plane_node_names()
    return names[0] if names else cluster_name()


def worker_node_names() -> list[str]:
    cp_names = set(control_plane_node_names())
    return [name for name in topology_node_names() if name not in cp_names]


def host_role_for_topology_node(node_name: str) -> str:
    """Assisted Installer host_role value for a topology node name."""
    return "master" if node_name in control_plane_node_names() else "worker"


def node_cdrom_image(node_name: str | None = None) -> str:
    nodes = _load_topology_manifest().get("content", {}).get("nodes", {})
    name = node_name or control_plane_node_name()
    cdrom = nodes.get(name, {}).get("cdrom")
    return str(cdrom) if cdrom else DEFAULT_DISCOVERY_ISO_NAME


def base_dns_domain() -> str:
    return (
        os.environ.get("BASE_DNS_DOMAIN", DEFAULT_BASE_DNS_DOMAIN).strip()
        or DEFAULT_BASE_DNS_DOMAIN
    )


def discovery_iso_path(*, must_exist: bool = False) -> Path:
    """Local path for the discovery ISO (download target or upload source)."""
    raw = os.environ.get("DISCOVERY_ISO_PATH") or os.environ.get("ISO_PATH")
    if raw:
        path = Path(raw).expanduser()
    else:
        cache = Path(__file__).resolve().parent.parent / ".cache"
        path = cache / "dsxair-discovery.iso"
    if must_exist and not path.is_file():
        raise SystemExit(
            f"Discovery ISO not found: {path}. Run 00_create_discovery_iso.py "
            "first, or set DISCOVERY_ISO_PATH=/path/to/discovery.iso."
        )
    return path


def blank_qcow2_path() -> Path:
    raw = os.environ.get("BLANK_QCOW2_PATH")
    if raw:
        return Path(raw).expanduser()
    cache = Path(__file__).resolve().parent.parent / ".cache"
    return cache / "blank-100g.qcow2"


def _optional_secret(env_name: str, *, default: str, what: str) -> str:
    """Resolve ENV / ENV_FILE, falling back to default when unset."""
    value = os.environ.get(env_name)
    if value:
        return value.strip()
    file_var = f"{env_name}_FILE"
    file_path = os.environ.get(file_var)
    if file_path:
        return _read_file(Path(file_path).expanduser(), what=what)
    return default


def jump_host_initial_password(*, image_default: str | None = None) -> str:
    """Factory password on first SSH to oob-mgmt-server (Air image default)."""
    value = os.environ.get("JUMP_HOST_INITIAL_PASSWORD", "").strip()
    if value:
        return value
    if image_default:
        return image_default.strip()
    return DEFAULT_JUMP_HOST_INITIAL_PASSWORD


def jump_host_password() -> str:
    """Target password after the mandatory first-login change on oob-mgmt-server."""
    return _optional_secret(
        "JUMP_HOST_PASSWORD",
        default=DEFAULT_JUMP_HOST_PASSWORD,
        what="jump host password",
    )

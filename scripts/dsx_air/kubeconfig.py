from __future__ import annotations

import socket
from pathlib import Path

import yaml

from dsx_air._bootstrap import ensure_scripts_path, repo_root


def kubeconfig_path(*, cluster_name: str) -> Path:
    return repo_root() / ".cache" / f"kubeconfig.{cluster_name}"


def tunneled_kubeconfig_path(*, cluster_name: str) -> Path:
    return repo_root() / ".cache" / f"kubeconfig.{cluster_name}.tunnel"


def api_hostname(*, cluster_name: str) -> str:
    ensure_scripts_path()
    import env_config  # noqa: E402

    return f"api.{cluster_name}.{env_config.base_dns_domain()}"


def host_is_loopback(host: str) -> bool:
    """True when host resolves only (or at least once) to localhost."""
    try:
        infos = socket.getaddrinfo(host, 6443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    loopback = {"127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}
    return any(info[4][0] in loopback for info in infos)


def for_local_oc(*, cluster_name: str) -> Path:
    """Kubeconfig that matches a working local ``oc`` (hosts file or ssh -L).

    If ``api.<cluster>.<domain>`` already points at loopback (laptop /etc/hosts
    + ssh -L), keep the downloaded kubeconfig. Rewriting the server to
    ``https://127.0.0.1:6443`` is what makes a raw TLS probe hang while ``oc``
    still works.
    """
    src = kubeconfig_path(cluster_name=cluster_name)
    if not src.is_file():
        raise FileNotFoundError(
            f"Kubeconfig not found: {src}. Run 07_install_cluster.py first."
        )
    if host_is_loopback(api_hostname(cluster_name=cluster_name)):
        return src
    return ensure_tunneled_kubeconfig(cluster_name=cluster_name)


def require_api(*, cluster_name: str, timeout: float = 45.0) -> tuple[Path | None, str]:
    """Return (kubeconfig, "") if ``oc get --raw /version`` succeeds."""
    try:
        path = for_local_oc(cluster_name=cluster_name)
    except (FileNotFoundError, ValueError) as exc:
        return None, str(exc)
    from dsx_air.oc_checks import raw_version

    result = raw_version(kubeconfig=str(path), timeout=timeout)
    if result.ok:
        return path, ""
    return None, result.reason or "oc get --raw /version failed"


def ensure_tunneled_kubeconfig(*, cluster_name: str) -> Path:
    """Patch local kubeconfig for SSH tunnel to API VIP on 127.0.0.1:6443."""
    src = kubeconfig_path(cluster_name=cluster_name)
    dst = tunneled_kubeconfig_path(cluster_name=cluster_name)
    if not src.is_file():
        raise FileNotFoundError(
            f"Kubeconfig not found: {src}. Run 07_install_cluster.py first."
        )

    cfg = yaml.safe_load(src.read_text())
    if not cfg or not cfg.get("clusters"):
        raise ValueError(f"Invalid kubeconfig: {src}")

    cluster = cfg["clusters"][0]["cluster"]
    cluster["server"] = "https://127.0.0.1:6443"
    cluster["insecure-skip-tls-verify"] = True
    cluster.pop("certificate-authority-data", None)
    cluster["tls-server-name"] = api_hostname(cluster_name=cluster_name)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(yaml.dump(cfg, default_flow_style=False))
    return dst

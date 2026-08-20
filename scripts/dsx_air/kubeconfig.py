from __future__ import annotations

from pathlib import Path

import yaml

from dsx_air._bootstrap import repo_root


def kubeconfig_path(*, cluster_name: str) -> Path:
    return repo_root() / ".cache" / f"kubeconfig.{cluster_name}"


def tunneled_kubeconfig_path(*, cluster_name: str) -> Path:
    return repo_root() / ".cache" / f"kubeconfig.{cluster_name}.tunnel"


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

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(yaml.dump(cfg, default_flow_style=False))
    return dst

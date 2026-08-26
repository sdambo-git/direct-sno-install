#!/usr/bin/env python3
"""
Verify an installed OpenShift cluster using a downloaded kubeconfig.

Uses the jump host when the API is only reachable on the OOB network.

If `oc` is not on PATH, downloads the Linux client from mirror.openshift.com
into `.cache/oc-client/` (same flow as:

    mkdir -p oc-4.12-test && cd oc-4.12-test
    curl -Ls https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.12.0/openshift-client-linux.tar.gz -o oc.tar.gz
    tar xzf oc.tar.gz oc
    ./oc version --client

Version defaults to 4.12.0; set OC_CLIENT_VERSION or OCP_VERSION to match
the cluster). The binary is copied to the jump host so `oc` need not be
installed there.

    uv run 08_verify_cluster.py
    KUBECONFIG=.cache/kubeconfig.ocp-cluster uv run 08_verify_cluster.py --local
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from air_common import ensure_jump_host_ready, get_api, get_simulation, jump_host_ssh_command
import env_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OC_CACHE_DIR = _REPO_ROOT / ".cache" / "oc-client"
_OC_MIRROR = (
    "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp"
)


def _kubeconfig_path() -> Path:
    env = os.environ.get("KUBECONFIG")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / ".cache" / f"kubeconfig.{env_config.cluster_name()}"


def _oc_url(version: str) -> str:
    return f"{_OC_MIRROR}/{version}/openshift-client-linux.tar.gz"


def ensure_oc() -> Path:
    """Return a local `oc` binary, downloading it if needed."""
    cached = _OC_CACHE_DIR / "oc"
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached
    on_path = shutil.which("oc")
    if on_path:
        return Path(on_path)

    version = env_config.oc_client_version()
    versions = [version]
    if version != env_config.DEFAULT_OC_CLIENT_VERSION:
        versions.append(env_config.DEFAULT_OC_CLIENT_VERSION)

    _OC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tarball = _OC_CACHE_DIR / "oc.tar.gz"
    last_error = ""
    for ver in versions:
        url = _oc_url(ver)
        print(f"Downloading oc {ver} from {url} ...")
        curl = subprocess.run(
            ["curl", "-fLs", url, "-o", str(tarball)],
            capture_output=True,
            text=True,
            check=False,
        )
        if curl.returncode != 0:
            last_error = (curl.stderr or curl.stdout or "").strip() or f"curl exit {curl.returncode}"
            print(f"  download failed for {ver}: {last_error}")
            continue
        extract = subprocess.run(
            ["tar", "xzf", str(tarball), "oc"],
            cwd=_OC_CACHE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if extract.returncode != 0 or not cached.is_file():
            last_error = (extract.stderr or extract.stdout or "").strip() or "tar did not produce oc"
            print(f"  extract failed for {ver}: {last_error}")
            continue
        cached.chmod(0o755)
        print(f"oc client ready at {cached}")
        subprocess.run([str(cached), "version", "--client"], check=True)
        return cached

    raise SystemExit(
        f"Could not download oc from mirror.openshift.com ({last_error}). "
        "Install the OpenShift client or set OC_CLIENT_VERSION to a published "
        f"folder under {_OC_MIRROR}/."
    )


def _run_oc(oc: Path, args: list[str], *, env: dict[str, str]) -> None:
    subprocess.run([str(oc), *args], env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run oc locally (only works if API is reachable from this machine).",
    )
    args = parser.parse_args()

    kubeconfig = _kubeconfig_path()
    if not kubeconfig.is_file():
        raise SystemExit(f"Kubeconfig not found: {kubeconfig}. Run 07_install_cluster.py first.")

    oc = ensure_oc()
    env = os.environ.copy()
    env["KUBECONFIG"] = str(kubeconfig)

    if args.local:
        _run_oc(oc, ["get", "nodes"], env=env)
        _run_oc(oc, ["get", "clusterversion"], env=env)
        return

    api = get_api()
    sim = get_simulation(api)
    service, server = ensure_jump_host_ready(sim)
    ssh = jump_host_ssh_command(service, server)
    print(f"Copying kubeconfig + oc to jump host and running via:\n\n    {ssh}\n")
    remote_kube = f"/tmp/kubeconfig.{env_config.cluster_name()}"
    remote_oc = "/tmp/oc"
    user = getattr(server.image, "default_username", None) or "ubuntu"
    ssh_opts = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    subprocess.run(
        [
            "scp",
            *ssh_opts,
            "-P",
            str(service.worker_port),
            str(kubeconfig),
            f"{user}@{service.worker_fqdn}:{remote_kube}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "scp",
            *ssh_opts,
            "-P",
            str(service.worker_port),
            str(oc),
            f"{user}@{service.worker_fqdn}:{remote_oc}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ssh",
            *ssh_opts,
            "-p",
            str(service.worker_port),
            f"{user}@{service.worker_fqdn}",
            f"chmod +x {remote_oc} && "
            f"KUBECONFIG={remote_kube} {remote_oc} get nodes && "
            f"KUBECONFIG={remote_kube} {remote_oc} get clusterversion",
        ],
        check=True,
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

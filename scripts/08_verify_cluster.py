#!/usr/bin/env python3
"""
Verify an installed OpenShift cluster using a downloaded kubeconfig.

Uses the jump host when the API is only reachable on the OOB network.

    uv run 08_verify_cluster.py
    KUBECONFIG=.cache/kubeconfig.sno-cluster uv run 08_verify_cluster.py --local
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from air_common import ensure_jump_host_ready, get_api, get_simulation, jump_host_ssh_command
import env_config


def _kubeconfig_path() -> Path:
    env = os.environ.get("KUBECONFIG")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / ".cache" / f"kubeconfig.{env_config.cluster_name()}"


def _run_oc(args: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(["oc", *args], env=env, check=True)


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

    env = os.environ.copy()
    env["KUBECONFIG"] = str(kubeconfig)

    if args.local:
        _run_oc(["get", "nodes"], env=env)
        _run_oc(["get", "clusterversion"], env=env)
        return

    api = get_api()
    sim = get_simulation(api)
    service, server = ensure_jump_host_ready(sim)
    ssh = jump_host_ssh_command(service, server)
    print(f"Copying kubeconfig to jump host and running oc via:\n\n    {ssh}\n")
    remote = "/tmp/kubeconfig.sno-cluster"
    user = getattr(server.image, "default_username", None) or "ubuntu"
    subprocess.run(
        [
            "scp",
            "-o",
            "BatchMode=yes",
            "-P",
            str(service.worker_port),
            str(kubeconfig),
            f"{user}@{service.worker_fqdn}:{remote}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-p",
            str(service.worker_port),
            f"{user}@{service.worker_fqdn}",
            f"KUBECONFIG={remote} oc get nodes && KUBECONFIG={remote} oc get clusterversion",
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

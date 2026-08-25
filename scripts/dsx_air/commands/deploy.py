"""Greenfield deploy from a LabSpec."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from dsx_air._bootstrap import ensure_scripts_path, repo_root
from dsx_air.pipeline import cache_dir, run_script
from dsx_air.spec import apply_to_environ, load_spec, preflight_auth
from dsx_air.topology import write_manifest

ensure_scripts_path()

from assisted_common import ai_call, get_client  # noqa: E402
from dsx_air.commands import destroy as destroy_cmd  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402
import air_common  # noqa: E402
import env_config  # noqa: E402


def _require_tools() -> None:
    if shutil.which("expect") is None:
        raise SystemExit("expect is not on PATH (needed for jump-host password bootstrap).")
    if shutil.which("qemu-img") is None:
        raise SystemExit("qemu-img is not on PATH (needed for blank-100g upload).")


def _probe_ai() -> None:
    ai = get_client(quiet=True)
    ai_call(ai, ai.list_clusters)
    print("Assisted Installer token: ok")


def _existing_sim(name: str):
    api = get_api()
    matches = [s for s in api.simulations.list(search=name) if s.name == name]
    return matches[0] if matches else None


def _ocp_node_count(sim) -> int:
    return len(air_common.get_topology_nodes(sim))


def run_deploy(
    *,
    spec_path: Path,
    sim: str | None = None,
    cluster: str | None = None,
    control_plane: int | None = None,
    workers: int | None = None,
    ocp_version: str | None = None,
    replace: bool = False,
    discovery_timeout: int | None = None,
) -> int:
    spec = load_spec(spec_path).merge(
        sim=sim,
        cluster=cluster,
        control_plane=control_plane,
        workers=workers,
        ocp_version=ocp_version,
    )
    preflight_auth(spec)
    _require_tools()

    cdrom = f"dsxair-discovery-{int(time.time())}"
    topo_path = cache_dir() / spec.simulation.name / "topology.json"
    write_manifest(spec, topo_path, cdrom=cdrom)
    apply_to_environ(spec, topology_path=topo_path)

    print(
        f"Deploy {spec.simulation.name!r} / cluster {spec.cluster.name!r} "
        f"({spec.cluster.control_plane.count} CP + {spec.cluster.workers.count} workers, "
        f"OCP {spec.cluster.version})"
    )
    _probe_ai()

    existing = _existing_sim(spec.simulation.name)
    expected = spec.expected_hosts
    if discovery_timeout is not None:
        os.environ["DISCOVERY_TIMEOUT"] = str(discovery_timeout)
    timeout_s = env_config.discovery_timeout_seconds(expected)
    if existing is not None:
        actual = _ocp_node_count(existing)
        if actual != expected:
            raise SystemExit(
                f"Simulation {spec.simulation.name!r} exists with {actual} OCP node(s); "
                f"spec expects {expected}. Pass --replace to destroy and recreate."
            )
        if replace:
            destroy_cmd.destroy_lab(spec, do_sim=True, do_cluster=True, force=True)
            existing = None
        else:
            print(
                f"Simulation {spec.simulation.name!r} already exists with {actual} node(s); "
                "reusing (pass --replace to wipe)."
            )
    elif replace:
        destroy_cmd.destroy_lab(spec, do_sim=True, do_cluster=True, force=True)

    if existing is None:
        run_script("00_create_discovery_iso.py", "--profile", spec.profile, "--force")
        run_script("upload_discovery_iso.py", "--name", cdrom)
        run_script("upload_blank_disk.py")
        run_script("01_create_simulation.py")
    else:
        run_script("04_create_jump_host_service.py")
        ai = get_client(quiet=True)
        try:
            from assisted_poll import get_cluster_dict

            cluster_state = get_cluster_dict(ai, spec.cluster.name).get("status")
        except Exception:  # noqa: BLE001
            cluster_state = ""
        if cluster_state == "installed":
            kube = repo_root() / ".cache" / f"kubeconfig.{spec.cluster.name}"
            print(f"Cluster already installed. Kubeconfig: {kube}")
            print("Next: uv run dsx-air console --spec", spec_path)
            return 0

    min_hosts = str(expected)
    print(f"Discovery wait: {timeout_s}s ({timeout_s // 60}m) for {expected} host(s)")
    run_script(
        "06_wait_for_host_ipv4.py",
        "--require-known",
        "--min-hosts",
        min_hosts,
        "--timeout",
        str(timeout_s),
    )
    run_script("07_install_cluster.py")

    kube = repo_root() / ".cache" / f"kubeconfig.{spec.cluster.name}"
    print(f"\nDeploy finished. Kubeconfig: {kube}")
    print("Next: uv run dsx-air console --spec", spec_path)
    return 0

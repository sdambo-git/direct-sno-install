"""Image-based install helpers for dsx-air ibi commands."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from dsx_air._bootstrap import ensure_scripts_path, repo_root
from dsx_air.pipeline import cache_dir, run_script
from dsx_air.spec import LabSpec, apply_to_environ, load_spec, remember_spec
from dsx_air.topology import os_image_for_disk_gb, write_manifest

ensure_scripts_path()

import air_common  # noqa: E402
import env_config  # noqa: E402
from upload_discovery_iso import find_image, get_api, upload_iso  # noqa: E402

DEFAULT_SEED_IMAGE = "quay.io/sdambo/ocp-seed:4.22.14"
DEFAULT_SEED_VERSION = "4.22.14"
DEFAULT_LIVE_IMAGE_NAME = "rhcos-ibi-4.22.14"
DEFAULT_CONFIG_IMAGE_NAME = "ibi-cluster-config-4.22.14"
DEFAULT_HOSTNAME = "ocp-cp-0"
DEFAULT_NODE = "ocp-cp-0"
OOB_NODE_IP = f"{env_config.OOB_IPV4_PREFIX}2"


def iso_workdir() -> Path:
    return repo_root() / "ibi-iso-workdir"


def config_workdir() -> Path:
    return repo_root() / "ibi-config-iso-workdir"


def live_iso_path() -> Path:
    raw = os.environ.get("IBI_LIVE_ISO_PATH", "").strip()
    return Path(raw).expanduser() if raw else iso_workdir() / "rhcos-ibi.iso"


def config_iso_path() -> Path:
    raw = os.environ.get("IBI_CONFIG_ISO_PATH", "").strip()
    return Path(raw).expanduser() if raw else config_workdir() / "imagebasedconfig.iso"


def find_openshift_install() -> Path:
    raw = os.environ.get("OPENSHIFT_INSTALL", "").strip()
    candidates = []
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.append(repo_root() / "openshift-install")
    which = shutil.which("openshift-install")
    if which:
        candidates.append(Path(which))
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise SystemExit(
        "openshift-install not found. Put the 4.22.14 binary at "
        f"{repo_root() / 'openshift-install'} or set OPENSHIFT_INSTALL=."
    )


def compact_pull_secret() -> str:
    raw = env_config.pull_secret_path().read_text().strip()
    return json.dumps(json.loads(raw), separators=(",", ":"))


def write_installation_config(
    dest: Path,
    *,
    seed_image: str = DEFAULT_SEED_IMAGE,
    seed_version: str = DEFAULT_SEED_VERSION,
) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "image-based-installation-config.yaml"
    data = {
        "apiVersion": "v1beta1",
        "kind": "ImageBasedInstallationConfig",
        "metadata": {"name": "dsx-ibi"},
        "seedImage": seed_image,
        "seedVersion": seed_version,
        "installationDisk": "/dev/vda",
        "extraPartitionLabel": "var-lib-containers",
        "extraPartitionStart": "100G",
        "extraPartitionNumber": 5,
        "sshKey": env_config.ssh_public_key(),
        "pullSecret": compact_pull_secret(),
    }
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    return path


def write_site_configs(dest: Path, spec: LabSpec, *, hostname: str = DEFAULT_HOSTNAME) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    install = {
        "apiVersion": "v1",
        "metadata": {"name": spec.cluster.name},
        "baseDomain": env_config.base_dns_domain(),
        "compute": [
            {
                "architecture": "amd64",
                "hyperthreading": "Enabled",
                "name": "worker",
                "replicas": 0,
            }
        ],
        "controlPlane": {
            "architecture": "amd64",
            "hyperthreading": "Enabled",
            "name": "master",
            "replicas": 1,
        },
        "networking": {"machineNetwork": [{"cidr": f"{env_config.OOB_IPV4_PREFIX}0/24"}]},
        "platform": {"none": {}},
        "fips": False,
        "pullSecret": compact_pull_secret(),
        "sshKey": env_config.ssh_public_key(),
    }
    (dest / "install-config.yaml").write_text(
        yaml.safe_dump(install, default_flow_style=False, sort_keys=False)
    )
    ibc = {
        "apiVersion": "v1beta1",
        "kind": "ImageBasedConfig",
        "metadata": {"name": spec.cluster.name},
        "hostname": hostname,
        "releaseRegistry": "quay.io",
    }
    (dest / "image-based-config.yaml").write_text(
        yaml.safe_dump(ibc, default_flow_style=False, sort_keys=False)
    )


def run_openshift_install(*args: str) -> None:
    binary = find_openshift_install()
    cmd = [str(binary), *args]
    print(f"+ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(repo_root()), check=False)
    if result.returncode != 0:
        raise SystemExit(f"openshift-install exited with status {result.returncode}")


def _upload_blank_disks(spec: LabSpec) -> None:
    sizes: set[int] = {spec.cluster.control_plane.disk_gb}
    if spec.cluster.workers.count:
        sizes.add(spec.cluster.workers.disk_gb)
    for disk_gb in sorted(sizes):
        image = os_image_for_disk_gb(disk_gb)
        if image == env_config.DEFAULT_BLANK_IMAGE_NAME:
            run_script("upload_blank_disk.py")
        elif image == env_config.DEFAULT_SNO_BLANK_IMAGE_NAME:
            run_script("upload_blank_disk_sno.py")
        else:
            run_script("upload_blank_disk.py", "--name", image, "--size", f"{int(disk_gb)}G")


def extract_config_iso(iso: Path, dest: Path) -> Path:
    if not iso.is_file():
        raise SystemExit(f"Config ISO not found: {iso}")
    dest.mkdir(parents=True, exist_ok=True)
    xorriso = shutil.which("xorriso")
    if xorriso is None:
        raise SystemExit("xorriso is not on PATH (needed to unpack imagebasedconfig.iso).")
    result = subprocess.run(
        [xorriso, "-osirrox", "on", "-indev", str(iso), "-extract", "/", str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"xorriso extract failed:\n{result.stderr}")
    cfg = dest / "cluster-configuration"
    if not cfg.is_dir():
        raise SystemExit(f"ISO {iso} has no cluster-configuration/ directory")
    return cfg


def apply_config_via_ssh(*, spec_path: Path | None, iso: Path | None = None) -> int:
    """Copy cluster-configuration onto the node (no CD-ROM / checkpoint change)."""
    from dsx_air.spec import activate_spec

    activate_spec(spec_path)
    api = get_api()
    sim = air_common.get_simulation(api)
    sim.refresh()
    if sim.state in {"BOOTING", "REQUESTING", "PROVISIONING"}:
        print(f"Waiting for {sim.name!r} to become ACTIVE (now {sim.state!r}) ...")
        air_common.wait_for_sim_state(sim, "ACTIVE", timeout=600)
    if sim.state != "ACTIVE":
        print(f"Starting {sim.name!r} ...")
        sim.start()
        air_common.wait_for_sim_state(sim, "ACTIVE", timeout=600)
    service, server = air_common.ensure_jump_host_ready(sim)
    host, port, username = air_common.jump_host_ssh_target(service, server)
    jump = f"{username}@{host}:{port}"
    src = extract_config_iso(iso or config_iso_path(), repo_root() / ".cache" / "ibi-config-extract")
    ssh_base = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ProxyJump={jump}",
    ]
    scp = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ProxyJump={jump}",
        "-r",
        str(src),
        f"core@{OOB_NODE_IP}:cluster-configuration",
    ]
    print(f"+ {' '.join(scp)}", flush=True)
    copied = subprocess.run(scp, check=False)
    if copied.returncode != 0:
        raise SystemExit(
            "scp to core failed. From the laptop use the IBI ssh key:\n"
            f"  scp -o ProxyJump={jump} -r {src} core@{OOB_NODE_IP}:~/cluster-configuration"
        )
    remote = (
        "sudo mkdir -p /opt/openshift && "
        "sudo rm -rf /opt/openshift/cluster-configuration && "
        "sudo cp -a \"$HOME/cluster-configuration\" /opt/openshift/cluster-configuration && "
        "sudo chmod -R a+rX /opt/openshift/cluster-configuration && "
        "ls -la /opt/openshift/cluster-configuration"
    )
    placed = subprocess.run(
        [*ssh_base, f"core@{OOB_NODE_IP}", remote],
        check=False,
    )
    if placed.returncode != 0:
        raise SystemExit("Failed to install /opt/openshift/cluster-configuration on the node.")
    print(
        "Wrote /opt/openshift/cluster-configuration (LCA looks for this or a "
        "cluster-config CD-ROM). Watch: journalctl -b -f"
    )
    return 0


def attach_cdrom(
    *,
    sim_name: str,
    node_name: str,
    image_name: str,
    iso: Path | None,
    replace: bool,
    skip_upload: bool,
) -> None:
    api = get_api()
    if skip_upload:
        image = find_image(api, image_name)
        if image is None:
            raise SystemExit(
                f"Air image {image_name!r} not found. Upload first or drop --skip-upload."
            )
        print(f"Using existing Air image {image_name!r} (id={image.id})")
    else:
        if iso is None or not iso.is_file():
            raise SystemExit(f"ISO not found: {iso}")
        image = upload_iso(api, name=image_name, filepath=iso, replace=replace)

    sim = air_common.get_simulation(api, sim_name)
    node = air_common.get_node(sim, node_name)
    print(
        f"Simulation {sim.name!r} state={sim.state!r} "
        f"node {node.name!r} cdrom={node.cdrom!r} "
        f"boot={((node.advanced or {}).get('boot'))!r}"
    )
    air_common.stop_simulation_and_wait_checkpoints(sim)
    node.refresh()
    advanced = dict(node.advanced or {})
    advanced["boot"] = ["hd", "cdrom"]
    if not advanced.get("cpu_mode"):
        advanced["cpu_mode"] = "host-passthrough"
    print(f"Attaching cdrom {image_name!r} ({image.id}) boot={advanced['boot']!r} ...")
    node.update(cdrom={"image": image.id}, advanced=advanced)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")
    air_common.start_simulation(sim)


def create_target(
    *,
    spec_path: Path,
    iso: Path,
    image_name: str,
    replace_sim: bool,
    replace_iso: bool,
) -> int:
    spec = load_spec(spec_path)
    apply_to_environ(spec)
    remember_spec(spec_path)
    if spec.expected_hosts != 1:
        raise SystemExit("IBI target spec must be 1 control-plane and 0 workers.")

    api = get_api()
    existing = next((s for s in api.simulations.list(search=spec.simulation.name) if s.name == spec.simulation.name), None)
    if existing is not None and not replace_sim:
        raise SystemExit(
            f"Simulation {spec.simulation.name!r} already exists. "
            "Pass --replace to delete it, or use `dsx-air ibi attach-live`."
        )
    if existing is not None and replace_sim:
        from dsx_air.commands.destroy import destroy_lab

        destroy_lab(spec, do_sim=True, do_cluster=False, force=True)

    if not iso.is_file():
        raise SystemExit(
            f"Live ISO not found: {iso}. Run `uv run dsx-air ibi image` first."
        )
    upload_iso(api, name=image_name, filepath=iso, replace=replace_iso)
    _upload_blank_disks(spec)

    topo = cache_dir() / spec.simulation.name / "topology.json"
    write_manifest(spec, topo, cdrom=image_name)
    apply_to_environ(spec, topology_path=topo)
    run_script("01_create_simulation.py")
    print(
        f"\nTarget {spec.simulation.name!r} is starting the live IBI ISO.\n"
        "Next: uv run dsx-air ibi wait-oob --spec "
        f"{spec_path}\n"
        "Watch Air console for IBI preparation, then:\n"
        f"  uv run dsx-air ibi config-image --spec {spec_path}\n"
        f"  uv run dsx-air ibi attach-config --spec {spec_path}"
    )
    return 0


def wait_oob(*, spec_path: Path | None, timeout: int) -> int:
    from dsx_air.spec import activate_spec

    spec = activate_spec(spec_path)
    api = get_api()
    name = spec.simulation.name if spec else env_config.simulation_name()
    sim = air_common.get_simulation(api, name)
    if sim.state != "ACTIVE":
        print(f"Starting {name!r} ...")
        sim.start()
        air_common.wait_for_sim_state(sim, "ACTIVE", timeout=600)
    service, server = air_common.ensure_jump_host_ready(sim)
    ssh = air_common.jump_host_ssh_command(service, server)
    print(f"Jump: {ssh}")
    host, port, username = air_common.jump_host_ssh_target(service, server)
    deadline = time.monotonic() + timeout
    while True:
        probe = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=10",
                "-p",
                str(port),
                f"{username}@{host}",
                "ping",
                "-c1",
                "-W2",
                OOB_NODE_IP,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            print(f"OOB ping {OOB_NODE_IP}: ok")
            print(
                "SSH to the node from the laptop:\n"
                f"  ssh -o ProxyJump={username}@{host}:{port} core@{OOB_NODE_IP}\n"
                "sshd may still be down during live-ISO install (connection refused)."
            )
            return 0
        if time.monotonic() > deadline:
            print(probe.stdout)
            print(probe.stderr)
            raise SystemExit(
                f"No ping to {OOB_NODE_IP} after {timeout}s. "
                "Check Air console (live ISO vs blank disk / no bootable device)."
            )
        print(f"waiting for ping {OOB_NODE_IP} ...")
        time.sleep(15)

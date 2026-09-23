"""Lab spec: simulation + cluster + auth file pointers."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, model_validator

_ENV_VAR = re.compile(r"\$\{([^}]+)\}")


class NodePool(BaseModel):
    count: int
    cpu: int = 16
    memory_mb: int = 65536
    disk_gb: int = 100


class ContainersPartitionSpec(BaseModel):
    """Day-0 Assisted extra MachineConfig for /var/lib/containers.

    ``gb: 0`` disables it. ``start_gb`` is left for RHCOS root (BIOS/boot +
    sysroot). The extra partition is ``gb`` GiB from that offset.
    """

    gb: int = 0
    start_gb: int = 100
    device: str = "/dev/vda"


class ClusterSpec(BaseModel):
    name: str
    version: str
    control_plane: NodePool
    workers: NodePool = Field(
        default_factory=lambda: NodePool(count=0, cpu=8, memory_mb=32768, disk_gb=100)
    )
    containers_partition: ContainersPartitionSpec = Field(
        default_factory=ContainersPartitionSpec
    )


class AuthSpec(BaseModel):
    air_api_key_file: str | None = None
    ai_offlinetoken_file: str | None = None
    pull_secret_file: str | None = None
    ssh_public_key_file: str | None = None


class SimulationSpec(BaseModel):
    name: str


class LabSpec(BaseModel):
    simulation: SimulationSpec
    cluster: ClusterSpec
    auth: AuthSpec = Field(default_factory=AuthSpec)

    def merge(
        self,
        *,
        sim: str | None = None,
        cluster: str | None = None,
        control_plane: int | None = None,
        workers: int | None = None,
        ocp_version: str | None = None,
    ) -> Self:
        data = self.model_dump()
        if sim is not None:
            data["simulation"]["name"] = sim
        if cluster is not None:
            data["cluster"]["name"] = cluster
        if control_plane is not None:
            data["cluster"]["control_plane"]["count"] = control_plane
        if workers is not None:
            data["cluster"]["workers"]["count"] = workers
        if ocp_version is not None:
            data["cluster"]["version"] = ocp_version
        return type(self).model_validate(data)

    @property
    def expected_hosts(self) -> int:
        return self.cluster.control_plane.count + self.cluster.workers.count

    @property
    def profile(self) -> str:
        if self.cluster.control_plane.count > 1 or self.cluster.workers.count:
            return "multinode"
        return "sno"

    @model_validator(mode="after")
    def _containers_partition_fits_disk(self) -> Self:
        part = self.cluster.containers_partition
        if part.gb <= 0:
            return self
        disk = self.cluster.control_plane.disk_gb
        need = part.start_gb + part.gb
        if disk < need:
            raise ValueError(
                f"cluster.containers_partition needs {need}GiB "
                f"(start_gb={part.start_gb} + gb={part.gb}) but "
                f"control_plane.disk_gb is {disk}"
            )
        return self


def expand_path(raw: str) -> Path:
    """Expand ~ and ${ENV} in a path string (not secret values)."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if not value:
            raise SystemExit(f"Environment variable {name} is unset in path {raw!r}.")
        return value

    expanded = _ENV_VAR.sub(_sub, raw)
    return Path(expanded).expanduser()


def load_spec(path: Path) -> LabSpec:
    text = path.read_text()
    suffix = path.suffix.lower()
    data: Any
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        import tomllib

        data = tomllib.loads(text)
    else:
        raise SystemExit(f"Unsupported spec format: {path.suffix} (use yaml, toml, or json)")
    if not isinstance(data, dict):
        raise SystemExit(f"Spec {path} must be a mapping.")
    return LabSpec.model_validate(data)


def last_spec_path() -> Path | None:
    """Absolute path of the spec last used by deploy or `--spec`."""
    from dsx_air.pipeline import cache_dir

    marker = cache_dir() / "last-spec"
    if not marker.is_file():
        return None
    raw = marker.read_text().strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def remember_spec(path: Path) -> None:
    from dsx_air.pipeline import cache_dir

    marker = cache_dir() / "last-spec"
    marker.write_text(str(path.resolve()) + "\n")


def activate_spec(spec_path: Path | None) -> LabSpec | None:
    """Load a lab spec into env (CLUSTER_NAME, SIMULATION_NAME, auth files).

    When ``spec_path`` is omitted, reuse ``.cache/last-spec`` from the last
    ``deploy`` / ``--spec``. If that is missing and exactly one generated
    lab exists under ``.cache/<sim>/topology.json``, use that simulation.
    """
    implicit = spec_path is None
    if spec_path is None:
        spec_path = last_spec_path()
    if spec_path is None:
        if implicit:
            _activate_single_cached_lab()
        return None
    from dsx_air.pipeline import cache_dir

    spec = load_spec(spec_path)
    preflight_auth(spec)
    topo = cache_dir() / spec.simulation.name / "topology.json"
    apply_to_environ(spec, topology_path=topo if topo.is_file() else None)
    if implicit:
        print(
            f"Using last spec {spec_path} "
            f"(simulation {spec.simulation.name}, cluster {spec.cluster.name})",
            file=sys.stderr,
        )
    else:
        remember_spec(spec_path)
    return spec


def _activate_single_cached_lab() -> bool:
    """If exactly one ``.cache/<sim>/topology.json`` exists, export its name."""
    from dsx_air.pipeline import cache_dir

    found = sorted(p for p in cache_dir().glob("*/topology.json") if p.is_file())
    if len(found) != 1:
        return False
    topo = found[0]
    try:
        name = json.loads(topo.read_text()).get("name")
    except (OSError, json.JSONDecodeError):
        return False
    if not name:
        return False
    os.environ["SIMULATION_NAME"] = str(name)
    os.environ["TOPOLOGY_PATH"] = str(topo)
    print(
        f"Using cached simulation {name!r} ({topo.parent.name}/topology.json). "
        "Pass --spec to choose a lab explicitly.",
        file=sys.stderr,
    )
    return True


def apply_to_environ(spec: LabSpec, *, topology_path: Path | None = None) -> None:
    """Export spec into env vars numbered scripts already read."""
    os.environ["CLUSTER_NAME"] = spec.cluster.name
    os.environ["SIMULATION_NAME"] = spec.simulation.name
    os.environ["OCP_VERSION"] = spec.cluster.version
    os.environ["CLUSTER_PROFILE"] = spec.profile
    os.environ["CONTROL_PLANE_COUNT"] = str(spec.cluster.control_plane.count)
    os.environ["EXPECTED_HOSTS"] = str(spec.expected_hosts)
    part = spec.cluster.containers_partition
    if part.gb > 0:
        os.environ["CONTAINERS_PARTITION_GB"] = str(part.gb)
        os.environ["CONTAINERS_PARTITION_START_GB"] = str(part.start_gb)
        os.environ["CONTAINERS_PARTITION_DEVICE"] = part.device
    else:
        for key in (
            "CONTAINERS_PARTITION_GB",
            "CONTAINERS_PARTITION_START_GB",
            "CONTAINERS_PARTITION_DEVICE",
        ):
            os.environ.pop(key, None)
    if topology_path is not None:
        os.environ["TOPOLOGY_PATH"] = str(topology_path)
    mapping = (
        ("air_api_key_file", "AIR_API_KEY_FILE"),
        ("ai_offlinetoken_file", "AI_OFFLINETOKEN_FILE"),
        ("pull_secret_file", "PULL_SECRET_PATH"),
        ("ssh_public_key_file", "SSH_PUBLIC_KEY_PATH"),
    )
    for field, env_name in mapping:
        raw = getattr(spec.auth, field)
        if raw:
            os.environ[env_name] = str(expand_path(raw))


def preflight_auth(spec: LabSpec) -> None:
    """Fail immediately if spec auth files are missing."""
    checks = (
        ("auth.air_api_key_file", spec.auth.air_api_key_file, "Air API key"),
        ("auth.ai_offlinetoken_file", spec.auth.ai_offlinetoken_file, "Assisted Installer offline token"),
        ("auth.pull_secret_file", spec.auth.pull_secret_file, "pull secret"),
        ("auth.ssh_public_key_file", spec.auth.ssh_public_key_file, "SSH public key"),
    )
    for key, raw, what in checks:
        if not raw:
            continue
        path = expand_path(raw)
        if not path.is_file():
            raise SystemExit(f"{what} file not found ({key}): {path}")
        if not path.read_text().strip() and key != "auth.pull_secret_file":
            raise SystemExit(f"{what} file is empty ({key}): {path}")
        if key == "auth.pull_secret_file" and path.stat().st_size == 0:
            raise SystemExit(f"{what} file is empty ({key}): {path}")

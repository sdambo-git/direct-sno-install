"""dsx-air ibi — image-based install (seed live ISO → target → config ISO)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from dsx_air import ibi
from dsx_air.spec import activate_spec, apply_to_environ, load_spec, remember_spec

app = typer.Typer(
    name="ibi",
    help="Image-based SNO: build ISOs, create the Air target, attach config ISO.",
    no_args_is_help=True,
)


def _exit(code: int) -> None:
    raise typer.Exit(code)


@app.command("image")
def image_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    directory: Path = typer.Option(None, "--dir", help="Workdir for rhcos-ibi.iso."),
    seed_image: str = typer.Option(ibi.DEFAULT_SEED_IMAGE, "--seed-image"),
    seed_version: str = typer.Option(ibi.DEFAULT_SEED_VERSION, "--seed-version"),
) -> None:
    """Write ImageBasedInstallationConfig (secrets from spec files) and create rhcos-ibi.iso."""
    if spec is not None:
        apply_to_environ(load_spec(spec))
        remember_spec(spec)
    dest = directory or ibi.iso_workdir()
    ibi.write_installation_config(dest, seed_image=seed_image, seed_version=seed_version)
    ibi.run_openshift_install("image-based", "create", "image", "--dir", str(dest))
    print(f"Live ISO: {dest / 'rhcos-ibi.iso'}")


@app.command("target")
def target_cmd(
    spec: Path = typer.Option(
        Path("examples/ibi-target.yaml"),
        "--spec",
        exists=True,
        readable=True,
        help="Target lab spec (not the seed).",
    ),
    iso: Optional[Path] = typer.Option(None, "--iso", help="Local rhcos-ibi.iso."),
    name: str = typer.Option(ibi.DEFAULT_LIVE_IMAGE_NAME, "--name", help="Air CD-ROM image name."),
    replace: bool = typer.Option(False, "--replace", help="Delete the Air sim if it exists."),
    replace_iso: bool = typer.Option(False, "--replace-iso", help="Re-upload the live ISO."),
) -> None:
    """Upload live ISO + blank disk, import a new Air sim, start it (no Assisted)."""
    _exit(
        ibi.create_target(
            spec_path=spec,
            iso=iso or ibi.live_iso_path(),
            image_name=name,
            replace_sim=replace,
            replace_iso=replace_iso,
        )
    )


@app.command("attach-live")
def attach_live_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    iso: Optional[Path] = typer.Option(None, "--iso"),
    name: str = typer.Option(ibi.DEFAULT_LIVE_IMAGE_NAME, "--name"),
    node: str = typer.Option(ibi.DEFAULT_NODE, "--node"),
    replace: bool = typer.Option(False, "--replace"),
    skip_upload: bool = typer.Option(False, "--skip-upload"),
) -> None:
    """Put the live IBI ISO in the CD-ROM without deleting disk checkpoints."""
    lab = activate_spec(spec)
    sim = lab.simulation.name if lab else "dsx-ibi-target"
    ibi.attach_cdrom(
        sim_name=sim,
        node_name=node,
        image_name=name,
        iso=None if skip_upload else (iso or ibi.live_iso_path()),
        replace=replace,
        skip_upload=skip_upload,
    )
    print("Live ISO attached. Wait for OOB ping, then IBI preparation in the console.")


@app.command("config-image")
def config_image_cmd(
    spec: Path = typer.Option(Path("examples/ibi-target.yaml"), "--spec", exists=True, readable=True),
    directory: Path = typer.Option(None, "--dir"),
    hostname: str = typer.Option(ibi.DEFAULT_HOSTNAME, "--hostname"),
) -> None:
    """Write install-config + ImageBasedConfig and create imagebasedconfig.iso."""
    lab = load_spec(spec)
    apply_to_environ(lab)
    remember_spec(spec)
    dest = directory or ibi.config_workdir()
    ibi.write_site_configs(dest, lab, hostname=hostname)
    ibi.run_openshift_install("image-based", "create", "config-image", "--dir", str(dest))
    print(f"Config ISO: {dest / 'imagebasedconfig.iso'}")
    print(f"Kubeconfig: {dest / 'auth' / 'kubeconfig'}")


@app.command("apply-config")
def apply_config_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    iso: Optional[Path] = typer.Option(None, "--iso"),
) -> None:
    """Copy cluster-configuration onto the node over SSH (no CD-ROM swap)."""
    ibi.apply_config_via_ssh(spec_path=spec, iso=iso)


@app.command("attach-config")
def attach_config_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    iso: Optional[Path] = typer.Option(None, "--iso"),
    name: str = typer.Option(ibi.DEFAULT_CONFIG_IMAGE_NAME, "--name"),
    node: str = typer.Option(ibi.DEFAULT_NODE, "--node"),
    replace: bool = typer.Option(False, "--replace"),
    skip_upload: bool = typer.Option(False, "--skip-upload"),
) -> None:
    """Attach imagebasedconfig.iso (cluster-config). Keeps hd checkpoints."""
    lab = activate_spec(spec)
    sim = lab.simulation.name if lab else "dsx-ibi-target"
    ibi.attach_cdrom(
        sim_name=sim,
        node_name=node,
        image_name=name,
        iso=None if skip_upload else (iso or ibi.config_iso_path()),
        replace=replace,
        skip_upload=skip_upload,
    )
    print(
        "Config ISO attached (hd first). On the node, sr0 should be cluster-config.\n"
        "When 6443 is up: export KUBECONFIG=ibi-config-iso-workdir/auth/kubeconfig"
    )


@app.command("wait-oob")
def wait_oob_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    timeout: int = typer.Option(1800, "--timeout", help="Seconds to wait for 192.168.200.2."),
) -> None:
    """Bootstrap jump host and wait until the target node answers ping."""
    ibi.wait_oob(spec_path=spec, timeout=timeout)

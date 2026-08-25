from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer

from dsx_air._bootstrap import ensure_scripts_path, repo_root
from dsx_air.commands import cluster, console, demo, deploy, destroy, operators, recover, start, status, tunnel_cmd

app = typer.Typer(
    name="dsx-air",
    help="OpenShift on NVIDIA DSX Air: deploy, operate, and open Console.",
    no_args_is_help=True,
    add_completion=False,
)


def _ensure_default_profile() -> None:
    if not os.environ.get("CLUSTER_PROFILE", "").strip():
        os.environ["CLUSTER_PROFILE"] = "multinode"


def _exit(code: int) -> None:
    raise typer.Exit(code)


@app.command("deploy")
def deploy_cmd(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True, help="Lab YAML/TOML/JSON spec."),
    sim: Optional[str] = typer.Option(None, "--sim", help="Override simulation.name."),
    cluster_name: Optional[str] = typer.Option(None, "--cluster", help="Override cluster.name."),
    control_plane: Optional[int] = typer.Option(None, "--control-plane", help="Override control_plane.count."),
    workers: Optional[int] = typer.Option(None, "--workers", help="Override workers.count."),
    ocp_version: Optional[str] = typer.Option(None, "--ocp-version", help="Override cluster.version."),
    replace: bool = typer.Option(False, "--replace", help="Destroy spec sim+cluster, then deploy."),
    discovery_timeout: Optional[int] = typer.Option(
        None,
        "--discovery-timeout",
        help="Minutes to wait for host discovery (default: max(20, 8 per host)).",
    ),
) -> None:
    """Create Assisted cluster, Air sim, install OpenShift, download kubeconfig."""
    _exit(
        deploy.run_deploy(
            spec_path=spec,
            sim=sim,
            cluster=cluster_name,
            control_plane=control_plane,
            workers=workers,
            ocp_version=ocp_version,
            replace=replace,
            discovery_timeout=discovery_timeout,
        )
    )


@app.command("destroy")
def destroy_cmd(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True),
    sim: bool = typer.Option(False, "--sim", help="Destroy the Air simulation from the spec."),
    cluster_flag: bool = typer.Option(False, "--cluster", help="Destroy the Assisted cluster from the spec."),
    force: bool = typer.Option(False, "--force", help="Skip the TTY confirmation prompt."),
) -> None:
    """Delete Air simulation and/or Assisted cluster named in the spec."""
    _exit(destroy.run_destroy(spec_path=spec, sim=sim, cluster=cluster_flag, force=force))


@app.command("recover")
def recover_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    node: Optional[str] = typer.Option(None, "--node", help="Topology node to recover."),
    reset_ai: bool = typer.Option(False, "--reset-ai", help="Also reset the Assisted cluster."),
) -> None:
    """Rebuild node disks and re-attach the discovery ISO."""
    _exit(recover.run_recover(spec_path=spec, node=node, reset_ai=reset_ai))


@app.command("console")
def console_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    print_only: bool = typer.Option(False, "--print-only", help="Print SSH and Chrome commands; do not exec."),
) -> None:
    """SOCKS + host Chrome to the OpenShift Web Console (jump-host /etc/hosts)."""
    _exit(console.run_console(spec_path=spec, print_only=print_only))


@app.command("start")
def start_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
) -> None:
    """Start simulation and prepare jump host."""
    _exit(start.run_start(spec_path=spec))


@app.command("status")
def status_cmd(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
) -> None:
    """Read-only readiness report with NEXT line."""
    _exit(status.run_status(spec_path=spec))


@app.command("demo")
def demo_cmd() -> None:
    """Compact status + cluster + operators (read-only)."""
    _exit(demo.run_demo())


@app.command("tunnel")
def tunnel_app(
    spec: Optional[Path] = typer.Option(None, "--spec", exists=True, readable=True),
    check: bool = typer.Option(False, "--check", help="Probe https://127.0.0.1:6443/version."),
    print_only: bool = typer.Option(False, "--print-only", help="Print the ssh command (default)."),
) -> None:
    """Print SSH tunnel command for oc/API."""
    _ = print_only
    _exit(tunnel_cmd.run_tunnel(check=check, spec_path=spec))


@app.command("cluster")
def cluster_cmd() -> None:
    """oc get nodes, clusterversion, machineconfigpool."""
    _exit(cluster.run_cluster())


@app.command("operators")
def operators_cmd() -> None:
    """Operator CSV and pod summary."""
    _exit(operators.run_operators())


def _run(argv: list[str] | None = None) -> int:
    _ensure_default_profile()
    os.chdir(repo_root())
    ensure_scripts_path()
    try:
        app(args=list(argv) if argv is not None else None, standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, str):
            print(code, file=sys.stderr)
            return 1
        return int(code)
    return 0


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(_run(argv))


if __name__ == "__main__":
    main()

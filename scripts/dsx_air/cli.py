from __future__ import annotations

import argparse
import os
import sys

from dsx_air._bootstrap import ensure_scripts_path, repo_root

ensure_scripts_path()

from dsx_air.commands import cluster, demo, operators, start, status, tunnel_cmd  # noqa: E402


def _ensure_default_profile() -> None:
    if not os.environ.get("CLUSTER_PROFILE", "").strip():
        os.environ["CLUSTER_PROFILE"] = "multinode"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsx-air",
        description="Demo CLI for an existing OpenShift lab on NVIDIA DSX Air.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Start simulation and prepare jump host")
    sub.add_parser("status", help="Read-only readiness report with NEXT line")
    sub.add_parser("demo", help="Compact status + cluster + operators (read-only)")

    tunnel = sub.add_parser("tunnel", help="Print SSH tunnel command for oc/API")
    tunnel.add_argument(
        "--check",
        action="store_true",
        help="Probe https://127.0.0.1:6443/version (tunnel must already be up)",
    )

    sub.add_parser("cluster", help="oc get nodes, clusterversion, machineconfigpool")
    sub.add_parser("operators", help="Operator CSV and pod summary")

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_default_profile()
    os.chdir(repo_root())
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "start":
        return start.run_start()
    if args.command == "status":
        return status.run_status()
    if args.command == "demo":
        return demo.run_demo()
    if args.command == "tunnel":
        return tunnel_cmd.run_tunnel(check=args.check)
    if args.command == "cluster":
        return cluster.run_cluster()
    if args.command == "operators":
        return operators.run_operators()

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

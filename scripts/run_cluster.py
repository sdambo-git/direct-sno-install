#!/usr/bin/env python3
"""
Interactive / scriptable orchestrator for the DSX Air OpenShift install flow
documented in ../README.md. Lets you pick which steps to run, in order,
instead of copy-pasting each `uv run ...` command by hand.

Respects the same env vars as every other script here (see README's Auth
table): AIR_API_KEY, AI_OFFLINETOKEN, PULL_SECRET_PATH, OCP_VERSION, and
CLUSTER_PROFILE. Set CLUSTER_PROFILE=multinode for the 3-node HA flow
(topology-multinode.json) — that's what this was written for — or leave it
unset/"sno" for the single-node flow (topology.json). Run from scripts/:

Interactive (menu, pick steps one at a time or run a range):
    uv run run_cluster.py

Non-interactive:
    uv run run_cluster.py --list
    uv run run_cluster.py --run 1,3-5
    uv run run_cluster.py --all --yes
    uv run run_cluster.py --from 6 --yes
    uv run run_cluster.py --recover ocp-cp-1

Every prompt has a sensible default; --yes (or piping stdin) accepts that
default instead of asking, so this is also safe to drop into CI.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import env_config

SCRIPTS_DIR = Path(__file__).resolve().parent


class StepFailed(RuntimeError):
    """Raised when an underlying script exits non-zero."""


@dataclass
class Ctx:
    yes: bool = False
    dry_run: bool = False
    continue_on_error: bool = False
    iso_name: str | None = None


def _noninteractive(ctx: Ctx) -> bool:
    return ctx.yes or not sys.stdin.isatty()


def _confirm(prompt: str, *, ctx: Ctx, default: bool) -> bool:
    if _noninteractive(ctx):
        return default
    suffix = "Y/n" if default else "y/N"
    try:
        answer = input(f"{prompt} [{suffix}] ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt(msg: str, *, ctx: Ctx, default: str) -> str:
    if _noninteractive(ctx):
        return default
    try:
        value = input(msg).strip()
    except EOFError:
        return default
    return value or default


def _run(*args: str, ctx: Ctx) -> None:
    script = SCRIPTS_DIR / args[0]
    print(f"\n$ uv run {' '.join(args)}\n")
    if ctx.dry_run:
        print("(dry-run: not executed)")
        return
    result = subprocess.run([sys.executable, str(script), *args[1:]], cwd=SCRIPTS_DIR)
    if result.returncode != 0:
        raise StepFailed(f"{args[0]} exited with status {result.returncode}")


def _update_topology_cdrom(iso_name: str) -> list[str]:
    """Point every topology node's `cdrom` at iso_name. Returns changed node names."""
    path = env_config.topology_path()
    data = json.loads(path.read_text())
    nodes = data.get("content", {}).get("nodes", {})
    changed = [name for name, node in nodes.items() if node.get("cdrom") != iso_name]
    for node in nodes.values():
        if "cdrom" in node:
            node["cdrom"] = iso_name
    if changed:
        path.write_text(json.dumps(data, indent=4) + "\n")
    return changed


# --- Steps -------------------------------------------------------------


def step_delete_ai(ctx: Ctx) -> bool:
    print("Deletes the Assisted Installer SaaS cluster + infraenv (not Air resources).")
    if not _confirm("Proceed with delete_assisted_cluster.py --yes?", ctx=ctx, default=False):
        print("Skipped.")
        return False
    _run("delete_assisted_cluster.py", "--yes", ctx=ctx)
    return True


def step_create_iso(ctx: Ctx) -> bool:
    force = _confirm(
        "Force recreate the Assisted Installer cluster/infraenv (--force)?",
        ctx=ctx,
        default=env_config.is_multinode(),
    )
    profile = env_config.cluster_profile()
    args = ["00_create_discovery_iso.py", "--profile", profile]
    if force:
        args.append("--force")
    _run(*args, ctx=ctx)
    return True


def step_upload_iso(ctx: Ctx) -> bool:
    if env_config.is_multinode():
        default_name = f"dsxair-discovery-{int(time.time())}"
        print(
            "Multinode profile: use a FRESH image name per ISO rebuild — Air "
            "won't let you overwrite an image already in use by nodes, and can "
            "serve a stale CDROM cache even when it's not."
        )
        iso_name = _prompt(
            f"Air image name to upload as [{default_name}]: ", ctx=ctx, default=default_name
        )
        ctx.iso_name = iso_name
        _run("upload_discovery_iso.py", "--name", iso_name, ctx=ctx)
        if ctx.dry_run:
            print(f"(dry-run: would update topology cdrom fields to {iso_name!r})")
            return True
        changed = _update_topology_cdrom(iso_name)
        if changed:
            print(f"Updated cdrom -> {iso_name!r} for nodes {changed} in {env_config.topology_path()}")
        else:
            print("Topology cdrom fields already up to date.")
    else:
        _run("upload_discovery_iso.py", ctx=ctx)
    return True


def step_upload_blank(ctx: Ctx) -> bool:
    _run("upload_blank_disk.py", ctx=ctx)
    return True


def step_verify_alignment(ctx: Ctx) -> bool:
    _run("verify_topology_alignment.py", ctx=ctx)
    return True


def step_create_simulation(ctx: Ctx) -> bool:
    _run("01_create_simulation.py", ctx=ctx)
    return True


def step_wait_hosts(ctx: Ctx) -> bool:
    min_hosts = env_config.expected_hosts()
    args = ["06_wait_for_host_ipv4.py", "--min-hosts", str(min_hosts)]
    if _confirm("Require hosts to be known/ready (--require-known)?", ctx=ctx, default=True):
        args.append("--require-known")
    _run(*args, ctx=ctx)
    return True


def step_assign_roles(ctx: Ctx) -> bool:
    if not env_config.is_multinode():
        print("SNO profile: role assignment is a no-op, skipping.")
        return False
    _run("assign_host_roles.py", ctx=ctx)
    return True


def step_configure_only(ctx: Ctx) -> bool:
    _run("07_install_cluster.py", "--configure-only", ctx=ctx)
    return True


def step_install(ctx: Ctx) -> bool:
    if not _confirm(
        "This starts the real OpenShift install and can take a long time. Continue?",
        ctx=ctx,
        default=True,
    ):
        print("Skipped.")
        return False
    _run("07_install_cluster.py", ctx=ctx)
    return True


def step_verify_cluster(ctx: Ctx) -> bool:
    _run("08_verify_cluster.py", ctx=ctx)
    return True


def action_recover_node(ctx: Ctx, node: str | None = None) -> None:
    names = env_config.topology_node_names()
    default = node or (names[0] if names else env_config.cluster_name())
    node_name = node or _prompt(f"Node to recover [{default}]: ", ctx=ctx, default=default)
    args = ["09_recover_to_discovery.py", "--node", node_name]
    if _confirm("Also reset the Assisted Installer cluster (--reset-ai)?", ctx=ctx, default=False):
        args.append("--reset-ai")
    _run(*args, ctx=ctx)


def action_jump_host(ctx: Ctx) -> None:
    _run("04_create_jump_host_service.py", ctx=ctx)


@dataclass
class Step:
    num: int
    title: str
    fn: Callable[[Ctx], bool]  # returns True if it actually ran, False if skipped


STEPS: list[Step] = [
    Step(1, "Delete existing Assisted Installer cluster (optional, destructive)", step_delete_ai),
    Step(2, "Create Assisted Installer cluster + download discovery ISO", step_create_iso),
    Step(3, "Upload discovery ISO to Air (fresh name for multinode + topology update)", step_upload_iso),
    Step(4, "Upload blank-100g disk image to Air", step_upload_blank),
    Step(5, "Verify topology image alignment (read-only)", step_verify_alignment),
    Step(6, "Import topology + start Air simulation", step_create_simulation),
    Step(7, "Wait for host discovery (OOB IPv4 from Assisted Installer)", step_wait_hosts),
    Step(8, "Assign host roles (master/worker, multinode only)", step_assign_roles),
    Step(9, "Configure cluster networking (gate, no install)", step_configure_only),
    Step(10, "Install the OpenShift cluster", step_install),
    Step(11, "Verify the installed cluster (oc get nodes)", step_verify_cluster),
]
STEPS_BY_NUM = {s.num: s for s in STEPS}


# --- Menu / runner -------------------------------------------------------


def _parse_step_spec(spec: str, *, max_num: int) -> list[int]:
    nums: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            nums.extend(range(int(a), int(b) + 1))
        else:
            nums.append(int(part))
    for n in nums:
        if not (1 <= n <= max_num):
            raise SystemExit(f"Step {n} out of range (1-{max_num}).")
    return nums


def _print_menu(status: dict[int, str]) -> None:
    profile = env_config.cluster_profile()
    print(f"\n=== DSX Air cluster orchestrator ({profile}) ===")
    print(f"Cluster: {env_config.cluster_name()!r}  Topology: {env_config.topology_path()}\n")
    for step in STEPS:
        mark = status.get(step.num, " ")
        print(f" {step.num:>2}  [{mark}]  {step.title}")
    print("  r        Recover a node back to discovery (ad hoc)")
    print("  j        Print jump-host SSH command (ad hoc)")
    print("\n([x]=ran  [-]=skipped/declined  [!]=failed)")
    print()


def run_steps(nums: list[int], ctx: Ctx, status: dict[int, str]) -> None:
    for n in nums:
        step = STEPS_BY_NUM[n]
        print(f"\n----- Step {n}: {step.title} -----")
        try:
            ran = step.fn(ctx)
            status[n] = "x" if ran else "-"
        except StepFailed as exc:
            status[n] = "!"
            print(f"\nStep {n} FAILED: {exc}")
            if ctx.continue_on_error:
                print("Continuing (--continue-on-error set).")
                continue
            if not _confirm("Continue with remaining steps anyway?", ctx=ctx, default=False):
                raise


def interactive_loop(ctx: Ctx) -> None:
    status: dict[int, str] = {}
    while True:
        _print_menu(status)
        try:
            choice = input(
                "Step number, range (e.g. 3-6), 'all' for all remaining, "
                "'r' to recover a node, 'j' for jump host, 'q' to quit: "
            ).strip().lower()
        except EOFError:
            print()
            return
        if choice in ("q", "quit", "exit"):
            return
        if choice in ("", "l", "list"):
            continue
        if choice == "r":
            try:
                action_recover_node(ctx)
            except StepFailed as exc:
                print(f"Recovery failed: {exc}")
            continue
        if choice == "j":
            try:
                action_jump_host(ctx)
            except StepFailed as exc:
                print(f"Jump host action failed: {exc}")
            continue
        if choice == "all":
            remaining = [s.num for s in STEPS if status.get(s.num) != "x"]
            try:
                run_steps(remaining, ctx, status)
            except StepFailed:
                pass
            continue
        try:
            nums = _parse_step_spec(choice, max_num=len(STEPS))
        except (ValueError, SystemExit) as exc:
            print(f"Could not parse {choice!r}: {exc}")
            continue
        try:
            run_steps(nums, ctx, status)
        except StepFailed:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list", action="store_true", help="List steps and exit.")
    parser.add_argument("--run", metavar="SPEC", help="Run steps non-interactively, e.g. '1,3-5'.")
    parser.add_argument("--all", action="store_true", help="Run every step in order, non-interactively.")
    parser.add_argument(
        "--from", dest="from_step", type=int, metavar="N", help="Run steps N..last, non-interactively."
    )
    parser.add_argument("--recover", metavar="NODE", help="Run node recovery for NODE and exit.")
    parser.add_argument("-y", "--yes", action="store_true", help="Accept the default answer for every prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument(
        "--continue-on-error", action="store_true", help="Keep going past a failed step instead of stopping."
    )
    args = parser.parse_args()

    if args.list:
        for step in STEPS:
            print(f"{step.num:>2}  {step.title}")
        return

    ctx = Ctx(yes=args.yes, dry_run=args.dry_run, continue_on_error=args.continue_on_error)

    if args.recover is not None:
        action_recover_node(ctx, node=args.recover)
        return

    if args.all:
        run_steps([s.num for s in STEPS], ctx, {})
        return

    if args.from_step is not None:
        run_steps([s.num for s in STEPS if s.num >= args.from_step], ctx, {})
        return

    if args.run:
        run_steps(_parse_step_spec(args.run, max_num=len(STEPS)), ctx, {})
        return

    if not sys.stdin.isatty():
        parser.print_help()
        raise SystemExit(
            "\nNo steps specified and stdin is not a TTY — pass --run/--all/--from, or run interactively."
        )

    interactive_loop(ctx)


if __name__ == "__main__":
    try:
        main()
    except StepFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

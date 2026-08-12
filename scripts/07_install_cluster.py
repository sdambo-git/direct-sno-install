#!/usr/bin/env python3
"""
Configure networking (if needed), start the Assisted Installer cluster install,
wait until installed, and download kubeconfig + kubeadmin password.

Run after 06_wait_for_host_ipv4.py succeeds.

During install, polls for action-required AI states. Does **not** toggle
boot/cdrom — the blank-disk topology uses permanent boot ``["hd", "cdrom"]``
(see README.md).

    uv run 07_install_cluster.py
    uv run 07_install_cluster.py --configure-only
    uv run 07_install_cluster.py --legacy-boot-to-disk   # discouraged
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from air_common import boot_node_to_disk, get_api, get_simulation
from assisted_common import cluster_hosts, get_client, primary_oob_ipv4
from assisted_poll import (
    PollSnapshot,
    PollTracker,
    analyze_hosts,
    check_air_post_boot_to_disk,
    format_issues,
    get_cluster_dict,
    host_stage,
    print_action_block,
    suggest_poll_interval,
)
import env_config


def _machine_network_cidr() -> str:
    raw = env_config.OOB_IPV4_PREFIX.rstrip(".")
    return f"{raw}.0/24"


def _configure_cluster(ai, name: str, oob_ip: str) -> None:
    cluster = ai.client.v2_get_cluster(cluster_id=ai.get_cluster_id(name)).to_dict()
    machine_networks = cluster.get("machine_networks") or []
    cidr = _machine_network_cidr()
    updates: dict = {}

    if not any(m.get("cidr") == cidr for m in machine_networks):
        updates["machine_networks"] = [cidr]

    is_sno = cluster.get("high_availability_mode") in (None, "None")
    if is_sno:
        if not updates:
            print("Cluster networking already configured (SNO; VIPs not required).")
            return
        print(f"Updating cluster {name!r}: {updates}")
        ai.update_cluster(name, updates)
        return

    api_vips = [v.get("ip") for v in (cluster.get("api_vips") or []) if v.get("ip")]
    ingress_vips = [v.get("ip") for v in (cluster.get("ingress_vips") or []) if v.get("ip")]
    if oob_ip not in api_vips or oob_ip not in ingress_vips:
        updates["api_vip"] = oob_ip
        updates["ingress_vip"] = oob_ip

    if not updates:
        print("Cluster networking already configured.")
        return

    print(f"Updating cluster {name!r}: {updates}")
    ai.update_cluster(name, updates)


def _wait_for_installable_host(ai, name: str, *, timeout: int = 600) -> dict:
    deadline = time.monotonic() + timeout
    tracker = PollTracker()
    poll_num = 0
    while True:
        poll_num += 1
        cluster = get_cluster_dict(ai, name)
        hosts = cluster_hosts(ai, name)
        issues = analyze_hosts(cluster, hosts)
        streak = tracker.record_issues([i for i in issues if i.severity == "action"])
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        for host in hosts:
            status = host.get("status")
            if status in {"known", "ready"}:
                return host
            if status in {"error", "cancelled"}:
                raise SystemExit(
                    f"Host {host.get('requested_hostname')} is {status!r}: "
                    f"{host.get('status_info')}"
                )

        summary = ", ".join(
            f"{h.get('requested_hostname')}={h.get('status')}" for h in hosts
        ) or "no hosts"
        print(f"  [{poll_num}] waiting for installable host ({summary})")

        abort, reason = tracker.should_abort(max_action_streak=6)
        if abort:
            raise SystemExit(f"{reason}\n{format_issues(issues)}")

        if time.monotonic() > deadline:
            raise SystemExit(f"Timed out waiting for installable host ({summary}).")
        time.sleep(15)


def _should_boot_to_disk(host: dict) -> bool:
    stage, _ = host_stage(host)
    return stage == "Rebooting"


def _wait_installed(
    ai,
    name: str,
    *,
    timeout: int,
    auto_boot_to_disk: bool,
    oob_ip: str,
    abort_after: int,
) -> None:
    deadline = time.monotonic() + timeout
    tracker = PollTracker()
    boot_to_disk_done = False
    poll_num = 0
    print(f"Waiting up to {timeout}s for cluster {name!r} to finish installing ...")

    while True:
        poll_num += 1
        cluster = get_cluster_dict(ai, name)
        status = cluster.get("status")
        completed = str(cluster.get("install_completed_at", "")) != "0001-01-01 00:00:00+00:00"
        hosts = cluster_hosts(ai, name)

        issues = analyze_hosts(cluster, hosts)
        if boot_to_disk_done:
            issues.extend(check_air_post_boot_to_disk())
            issues.extend(tracker.check_oob_after_boot(oob_ip))
        streak = tracker.record_issues([i for i in issues if i.severity == "action"])
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        host_summary = []
        for host in hosts:
            stage, info = host_stage(host)
            host_summary.append(
                f"{host.get('requested_hostname')} status={host.get('status')} "
                f"stage={stage!r} info={info!r}"
            )
            if auto_boot_to_disk and not boot_to_disk_done and _should_boot_to_disk(host):
                print(
                    f"Host reached {stage!r}; switching Air boot order to disk ...",
                    flush=True,
                )
                sim = get_simulation(get_api())
                boot_node_to_disk(sim, force=True)
                boot_to_disk_done = True
                tracker.note_boot_to_disk()
                post_issues = check_air_post_boot_to_disk()
                if post_issues:
                    print_action_block(post_issues)

        print(f"  [{poll_num}] cluster status={status!r} install_completed={completed}", flush=True)
        if host_summary:
            print(f"  {'; '.join(host_summary)}")

        if completed or status == "installed":
            print("Cluster install completed.")
            return
        if status in {"error", "cancelled"}:
            raise SystemExit(
                f"Cluster entered state {status!r}.\n{format_issues(issues)}"
            )

        abort, reason = tracker.should_abort(max_action_streak=abort_after)
        if abort:
            raise SystemExit(
                f"Install needs attention — stopping after repeated alerts.\n"
                f"{reason}\n{format_issues(issues)}"
            )

        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {timeout}s waiting for install to complete.\n"
                f"{format_issues(issues)}"
            )

        snapshot = PollSnapshot(cluster_status=status or "", hosts=hosts)
        time.sleep(suggest_poll_interval(snapshot))


def _download_credentials(ai, name: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    ai.download_kubeconfig(name, str(dest))
    ai.download_kubeadminpassword(name, str(dest))
    print(f"Downloaded kubeconfig and kubeadmin password into {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configure-only",
        action="store_true",
        help="Only apply machine network / VIP updates; do not start install.",
    )
    parser.add_argument(
        "--legacy-boot-to-disk",
        action="store_true",
        help="Run deprecated boot-to-disk at Rebooting (not recommended; see README).",
    )
    parser.add_argument(
        "--install-timeout",
        type=int,
        default=7200,
        help="Seconds to wait for install completion (default: 7200).",
    )
    parser.add_argument(
        "--abort-after",
        type=int,
        default=3,
        help="Exit after this many consecutive action-required polls (default: 3).",
    )
    parser.add_argument(
        "--oob-ip",
        help="Override detected OOB IPv4 (default: from host inventory).",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    ai = get_client(quiet=False)
    oob_ip = args.oob_ip or primary_oob_ipv4(ai, name)
    print(f"Using OOB IPv4 {oob_ip} for machine network / VIP configuration.")

    host = _wait_for_installable_host(ai, name)
    print(
        f"Host {host.get('requested_hostname')!r} is {host.get('status')!r} "
        f"({host.get('status_info')})."
    )

    _configure_cluster(ai, name, oob_ip)

    if args.configure_only:
        print("Configure-only mode; not starting install.")
        return

    cluster = get_cluster_dict(ai, name)
    if cluster.get("status") not in {"ready", "installing"}:
        print(f"Cluster status is {cluster.get('status')!r}; waiting for ready ...")
        while cluster.get("status") != "ready":
            time.sleep(10)
            cluster = get_cluster_dict(ai, name)

    auto_boot = args.legacy_boot_to_disk
    if auto_boot:
        print("Starting cluster install (legacy boot-to-disk enabled — not recommended).")
    else:
        print(
            "Starting cluster install (boot order unchanged; blank hd → cdrom, "
            "then installed hd wins on reboot)."
        )
    ai.start_cluster(name)
    _wait_installed(
        ai,
        name,
        timeout=args.install_timeout,
        auto_boot_to_disk=auto_boot,
        oob_ip=oob_ip,
        abort_after=args.abort_after,
    )

    cache = Path(__file__).resolve().parent.parent / ".cache"
    _download_credentials(ai, name, cache)
    print(
        f"\nNext: verify with kubeconfig at {cache / f'kubeconfig.{name}'} "
        f"(run 08_verify_cluster.py via jump host if needed)."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

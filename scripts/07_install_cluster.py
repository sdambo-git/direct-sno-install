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

from air_common import boot_node_to_disk, default_node_name, get_api, get_simulation
from assisted_common import (
    ai_call,
    assign_topology_roles,
    cluster_hosts,
    get_client,
    host_oob_ipv4s,
    refresh_ai,
)
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


def _configure_cluster(ai, name: str) -> None:
    cluster = ai_call(
        ai, lambda: ai.client.v2_get_cluster(cluster_id=ai.get_cluster_id(name)).to_dict()
    )
    machine_networks = cluster.get("machine_networks") or []
    cidr = _machine_network_cidr()
    updates: dict = {}

    if not any(m.get("cidr") == cidr for m in machine_networks):
        updates["machine_networks"] = [cidr]

  # Multinode and HA clusters need API/Ingress VIPs unless user-managed networking.
    user_managed = bool(cluster.get("user_managed_networking"))
    needs_vips = (env_config.is_multinode() or cluster.get(
        "high_availability_mode"
    ) not in (None, "None")) and not user_managed
    if not needs_vips:
        if not updates:
            if user_managed and env_config.is_multinode():
                print(
                    "Cluster networking already configured "
                    "(user-managed networking; VIPs not set via AI)."
                )
            else:
                print("Cluster networking already configured (SNO; VIPs not required).")
            return
        print(f"Updating cluster {name!r}: {updates}")
        ai_call(ai, lambda: ai.update_cluster(name, updates))
        return

    api_vip = env_config.api_vip()
    ingress_vip = env_config.ingress_vip()
    api_vips = [v.get("ip") for v in (cluster.get("api_vips") or []) if v.get("ip")]
    ingress_vips = [v.get("ip") for v in (cluster.get("ingress_vips") or []) if v.get("ip")]
    if api_vip not in api_vips:
        updates["api_vip"] = api_vip
    if ingress_vip not in ingress_vips:
        updates["ingress_vip"] = ingress_vip

    if not updates:
        print("Cluster networking already configured.")
        return

    print(f"Updating cluster {name!r}: {updates}")
    ai_call(ai, lambda: ai.update_cluster(name, updates))


def _assign_host_roles(ai) -> None:
    assign_topology_roles(ai)


def _wait_for_installable_hosts(ai, name: str, *, timeout: int = 900) -> list[dict]:
    required = env_config.expected_hosts()
    deadline = time.monotonic() + timeout
    tracker = PollTracker()
    poll_num = 0
    while True:
        poll_num += 1
        cluster = get_cluster_dict(ai, name)
        hosts = cluster_hosts(ai, name)
        issues = analyze_hosts(cluster, hosts)
        streak = tracker.record_issues(issues)
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        ready = [
            h for h in hosts
            if h.get("status") in {"known", "ready"} and host_oob_ipv4s(h)
        ]
        for host in hosts:
            status = host.get("status")
            if status in {"error", "cancelled"}:
                raise SystemExit(
                    f"Host {host.get('requested_hostname')} is {status!r}: "
                    f"{host.get('status_info')}"
                )

        summary = ", ".join(
            f"{h.get('requested_hostname')}={h.get('status')}" for h in hosts
        ) or "no hosts"
        if len(ready) >= required:
            print(f"All {len(ready)} required host(s) are installable:")
            for host in ready:
                ips = host_oob_ipv4s(host)
                print(
                    f"  - {host.get('requested_hostname')}: "
                    f"status={host.get('status')} oob={ips[0] if ips else '-'}"
                )
            return ready

        print(
            f"  [{poll_num}] waiting for {required} installable host(s) "
            f"({len(ready)}/{required} ready; {summary})"
        )

        abort, reason = tracker.should_abort(max_action_streak=6)
        if abort:
            raise SystemExit(f"{reason}\n{format_issues(issues)}")

        if time.monotonic() > deadline:
            raise SystemExit(f"Timed out waiting for installable hosts ({summary}).")
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
    boot_to_disk_done: set[str] = set()
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
        streak = tracker.record_issues(issues)
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        host_summary = []
        for host in hosts:
            hostname = host.get("requested_hostname") or host.get("id")
            stage, info = host_stage(host)
            host_summary.append(
                f"{hostname} status={host.get('status')} "
                f"stage={stage!r} info={info!r}"
            )
            if (
                auto_boot_to_disk
                and hostname not in boot_to_disk_done
                and _should_boot_to_disk(host)
            ):
                print(
                    f"Host {hostname} reached {stage!r}; switching Air boot order to disk ...",
                    flush=True,
                )
                sim = get_simulation(get_api())
                boot_node_to_disk(sim, node_name=default_node_name(), force=True)
                boot_to_disk_done.add(hostname)
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
    refresh_ai(ai)
    ai_call(ai, lambda: ai.download_kubeconfig(name, str(dest)))
    ai_call(ai, lambda: ai.download_kubeadminpassword(name, str(dest)))
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
        help="Override detected OOB IPv4 for post-boot polling (default: first host).",
    )
    parser.add_argument(
        "--skip-role-assignment",
        action="store_true",
        help="Skip automatic host role assignment (if assign_host_roles.py was run).",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    ai = get_client(quiet=False)

    ready_hosts = _wait_for_installable_hosts(ai, name)
    if not args.skip_role_assignment:
        _assign_host_roles(ai)

    if env_config.is_multinode():
        print(
            f"Multinode networking: machine network {_machine_network_cidr()}, "
            f"API VIP {env_config.api_vip()}, Ingress VIP {env_config.ingress_vip()}."
        )
    else:
        oob_ip = args.oob_ip or host_oob_ipv4s(ready_hosts[0])[0]
        print(f"Using OOB IPv4 {oob_ip} for machine network configuration.")

    _configure_cluster(ai, name)

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
    ai_call(ai, lambda: ai.start_cluster(name))

    oob_ip = args.oob_ip or host_oob_ipv4s(ready_hosts[0])[0]
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
    verify_target = env_config.api_vip() if env_config.is_multinode() else oob_ip
    print(
        f"\nNext: verify with kubeconfig at {cache / f'kubeconfig.{name}'}\n"
        f"Tunnel example (multinode): ssh -N -L 127.0.0.1:6443:{verify_target}:6443 "
        f"<jump-host-ssh-command>\n"
        f"  oc get nodes --server=https://127.0.0.1:6443 --insecure-skip-tls-verify"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

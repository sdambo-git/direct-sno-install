#!/usr/bin/env python3
"""
Wait until Assisted Installer reports hosts for the lab cluster with real
Air OOB IPv4 addresses (192.168.200.0/24). Does not start the cluster install.

Polls Assisted Installer and (when AIR_API_KEY is set) checks Air boot order
so discovery failures surface early with remediation hints.

    uv run 06_wait_for_host_ipv4.py
    uv run 06_wait_for_host_ipv4.py --require-known --min-hosts 2

Pins Assisted host roles from topology names (ocp-cp-* = master, ocp-worker-* = worker)
as soon as each host hostname is known. Does not start the cluster install.
"""
from __future__ import annotations

import argparse
import sys
import time

from assisted_common import (
    all_hosts_oob_ready,
    assign_topology_roles,
    cluster_hosts,
    get_client,
    host_oob_ipv4s,
)
from assisted_poll import (
    PollSnapshot,
    PollTracker,
    analyze_hosts,
    check_air_discovery_boot,
    format_issues,
    get_cluster_dict,
    print_action_block,
    suggest_poll_interval,
)
import env_config


def _summarize_hosts(hosts: list[dict]) -> str:
    parts = []
    for host in hosts:
        hostname = host.get("requested_hostname") or host.get("id")
        status = host.get("status")
        ips = host_oob_ipv4s(host)
        parts.append(f"{hostname} status={status} oob={ips or '-'}")
    return "; ".join(parts) if parts else "no hosts yet"


def _elapsed_label(started: float) -> str:
    elapsed = max(0, int(time.monotonic() - started))
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes}m{seconds:02d}s"


def _resume_hint(*, min_hosts: int, timeout: int) -> str:
    return (
        "Resume without recreating the sim:\n"
        f"  uv run 06_wait_for_host_ipv4.py --require-known --min-hosts {min_hosts} "
        f"--timeout {timeout}\n"
        "  uv run 07_install_cluster.py"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds to wait for hosts (default: max(20m, 8m per expected host)).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Base polling interval in seconds (default: 15).",
    )
    parser.add_argument(
        "--require-known",
        action="store_true",
        help="Exit only when host status is known/ready (not just OOB IP).",
    )
    parser.add_argument(
        "--min-hosts",
        type=int,
        default=None,
        help="Minimum hosts with OOB IPv4 (default: EXPECTED_HOSTS / topology size).",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    min_hosts = args.min_hosts if args.min_hosts is not None else env_config.expected_hosts()
    timeout = args.timeout if args.timeout is not None else env_config.discovery_timeout_seconds(min_hosts)
    no_hosts_limit = min(env_config.no_hosts_abort_seconds(), timeout)
    ai = get_client(quiet=True)
    started = time.monotonic()
    deadline = started + timeout
    tracker = PollTracker()
    last_summary = "no hosts yet"
    poll_num = 0
    saw_any_host = False

    print(
        f"Waiting up to {timeout}s ({timeout // 60}m) for {min_hosts} host(s) on cluster {name!r} "
        f"with IPv4 in {env_config.OOB_IPV4_PREFIX}0/24 "
        f"(abort after {no_hosts_limit}s if no hosts appear) ..."
    )

    while True:
        poll_num += 1
        cluster = get_cluster_dict(ai, name)
        hosts = cluster_hosts(ai, name)
        if hosts:
            saw_any_host = True
            assign_topology_roles(ai, hosts)
        issues = analyze_hosts(cluster, hosts) + check_air_discovery_boot()
        streak = tracker.record_issues(issues)
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        for host in hosts:
            hostname = host.get("requested_hostname") or host.get("id")
            status = host.get("status")
            if status in {"error", "cancelled"}:
                raise SystemExit(
                    f"Host {hostname} needs recovery before discovery can continue: "
                    f"{status!r} — {host.get('status_info')}\n"
                    f"{format_issues(issues)}"
                )

        ready, ready_hosts = all_hosts_oob_ready(
            ai, min_hosts=min_hosts, require_known=args.require_known
        )
        last_summary = _summarize_hosts(hosts)
        ready_n = 0
        for h in hosts:
            ips = host_oob_ipv4s(h)
            if not ips:
                continue
            if args.require_known and h.get("status") not in {"known", "ready"}:
                continue
            ready_n += 1

        if any((h.get("status") or "") == "resetting-pending-user-action" for h in hosts):
            resetting = [
                h.get("requested_hostname") or h.get("id")
                for h in hosts
                if h.get("status") == "resetting-pending-user-action"
            ]
            print(
                f"  [{poll_num}] {_elapsed_label(started)} elapsed, "
                f"{len(hosts)}/{min_hosts} hosts ({', '.join(str(x) for x in resetting)} resetting)"
            )
        elif ready:
            print(f"Host discovery succeeded for {len(ready_hosts)} host(s):")
            for host in ready_hosts:
                hostname = host.get("requested_hostname") or host.get("id")
                ips = host_oob_ipv4s(host)
                print(
                    f"  - {hostname}: OOB {ips[0]} (status={host.get('status')})"
                )
            if not args.require_known:
                print(
                    "Note: not all hosts may be known/ready yet — "
                    "pass --require-known before install."
                )
            print("Next: uv run scripts/07_install_cluster.py when all hosts are known/ready.")
            return
        else:
            print(
                f"  [{poll_num}] {_elapsed_label(started)} elapsed, "
                f"{ready_n}/{min_hosts} ready ({last_summary})",
                flush=True,
            )

        abort, reason = tracker.should_abort(
            max_action_streak=12 if any(
                (h.get("status") or "") == "resetting-pending-user-action" for h in hosts
            ) else 4,
        )
        if abort:
            raise SystemExit(f"Stopping: {reason}\n{format_issues(issues)}")

        elapsed = time.monotonic() - started
        if not saw_any_host and elapsed > no_hosts_limit:
            raise SystemExit(
                f"No Assisted Installer hosts after {int(elapsed)}s. "
                "Check Air UI: sim ACTIVE, nodes booting discovery ISO (not 'No bootable device'), "
                "and topology cdrom matches the uploaded image.\n"
                f"{format_issues(issues + check_air_discovery_boot())}\n"
                f"{_resume_hint(min_hosts=min_hosts, timeout=timeout)}"
            )

        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {timeout}s waiting for {min_hosts} host(s) with OOB IPv4 "
                f"(last seen: {last_summary}).\n"
                f"{format_issues(issues + check_air_discovery_boot())}\n"
                f"{_resume_hint(min_hosts=min_hosts, timeout=timeout)}"
            )

        interval = suggest_poll_interval(
            PollSnapshot(cluster_status=cluster.get("status", ""), hosts=hosts)
        )
        time.sleep(min(args.interval, interval) if not hosts else interval)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

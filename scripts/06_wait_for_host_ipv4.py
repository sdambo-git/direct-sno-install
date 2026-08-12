#!/usr/bin/env python3
"""
Wait until Assisted Installer reports a host for the lab cluster with a real
Air OOB IPv4 (192.168.200.0/24). Does not start the cluster install.

Polls Assisted Installer and (when AIR_API_KEY is set) checks Air boot order
so discovery failures surface early with remediation hints.

    uv run 06_wait_for_host_ipv4.py
"""
from __future__ import annotations

import argparse
import sys
import time

from assisted_common import cluster_hosts, get_client, host_oob_ipv4s
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Seconds to wait before failing (default: 900).",
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
    args = parser.parse_args()

    name = env_config.cluster_name()
    ai = get_client(quiet=True)
    deadline = time.monotonic() + args.timeout
    tracker = PollTracker()
    last_summary = "no hosts yet"
    poll_num = 0

    print(
        f"Waiting up to {args.timeout}s for a host on cluster {name!r} "
        f"with IPv4 in {env_config.OOB_IPV4_PREFIX}0/24 ..."
    )

    while True:
        poll_num += 1
        cluster = get_cluster_dict(ai, name)
        hosts = cluster_hosts(ai, name)
        issues = analyze_hosts(cluster, hosts) + check_air_discovery_boot()
        streak = tracker.record_issues([i for i in issues if i.severity == "action"])
        if issues:
            print_action_block(issues, consecutive=max(streak, 1))

        summaries = []
        for host in hosts:
            hostname = host.get("requested_hostname") or host.get("id")
            status = host.get("status")
            ips = host_oob_ipv4s(host)
            summaries.append(f"{hostname} status={status} oob={ips or '-'}")

            if status in {"error", "cancelled"}:
                raise SystemExit(
                    f"Host {hostname} needs recovery before discovery can continue: "
                    f"{status!r} — {host.get('status_info')}\n"
                    f"{format_issues(issues)}"
                )

            if status == "resetting-pending-user-action":
                print(
                    f"  [{poll_num}] {hostname} resetting — waiting for discovery ISO boot "
                    f"({host.get('status_info')})"
                )
                break

            if ips:
                if args.require_known and status not in {"known", "ready"}:
                    print(
                        f"  [{poll_num}] {hostname} has OOB {ips[0]} but status={status!r}; "
                        f"waiting for known/ready ..."
                    )
                    break
                print(
                    f"Host discovery succeeded: {hostname} has OOB IPv4 "
                    f"{ips[0]} (status={status})."
                )
                if status not in {"known", "ready"}:
                    print(
                        f"Note: status is {status!r}, not known/ready yet — "
                        "run install only when validations pass."
                    )
                print("Next: uv run scripts/07_install_cluster.py when host is known/ready.")
                return
        else:
            last_summary = "; ".join(summaries) if summaries else "no hosts yet"
            print(f"  [{poll_num}] still waiting ({last_summary})")

        abort, reason = tracker.should_abort(
            max_action_streak=12 if any(
                (h.get("status") or "") == "resetting-pending-user-action" for h in hosts
            ) else 4
        )
        if abort:
            raise SystemExit(f"Stopping: {reason}\n{format_issues(issues)}")

        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {args.timeout}s waiting for OOB IPv4 "
                f"(last seen: {last_summary}).\n"
                f"{format_issues(issues + check_air_discovery_boot())}"
            )

        snapshot_hosts = hosts
        interval = suggest_poll_interval(
            PollSnapshot(cluster_status=cluster.get("status", ""), hosts=hosts)
        )
        time.sleep(min(args.interval, interval) if not snapshot_hosts else interval)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

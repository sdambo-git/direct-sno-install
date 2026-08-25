#!/usr/bin/env python3
"""
Wait until Assisted Installer reports hosts for the lab cluster with real
Air OOB IPv4 addresses (192.168.200.0/24). Does not start the cluster install.

Polls Assisted Installer and (when AIR_API_KEY is set) checks Air boot order
so discovery failures surface early with remediation hints.

    uv run 06_wait_for_host_ipv4.py
    uv run 06_wait_for_host_ipv4.py --require-known --min-hosts 2
"""
from __future__ import annotations

import argparse
import sys
import time

from assisted_common import (
    all_hosts_oob_ready,
    cluster_hosts,
    ensure_additional_ntp,
    get_client,
    host_oob_ipv4s,
    refresh_ai_token,
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
    parser.add_argument(
        "--min-hosts",
        type=int,
        default=None,
        help="Minimum hosts with OOB IPv4 (default: EXPECTED_HOSTS / topology size).",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    min_hosts = args.min_hosts if args.min_hosts is not None else env_config.expected_hosts()
    ai = get_client(quiet=True)
    deadline = time.monotonic() + args.timeout
    tracker = PollTracker()
    last_summary = "no hosts yet"
    poll_num = 0
    ntp_patched = False

    print(
        f"Waiting up to {args.timeout}s for {min_hosts} host(s) on cluster {name!r} "
        f"with IPv4 in {env_config.OOB_IPV4_PREFIX}0/24 ..."
    )

    while True:
        poll_num += 1
        try:
            cluster = get_cluster_dict(ai, name)
            hosts = cluster_hosts(ai, name)
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "401" in text or "token is invalid" in text.lower():
                print(f"  [{poll_num}] AI token expired; refreshing ...")
                refresh_ai_token(ai)
                continue
            raise
        issues = analyze_hosts(cluster, hosts) + check_air_discovery_boot()
        if any("ntp" in (i.message + i.code).lower() for i in issues) and not ntp_patched:
            ntp_patched = True
            try:
                ensure_additional_ntp(ai, name)
            except Exception as exc:  # noqa: BLE001
                print(f"  could not set additional_ntp_source: {env_config.describe_error(exc)}")
        streak = tracker.record_issues([i for i in issues if i.severity == "action"])
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

        if any((h.get("status") or "") == "resetting-pending-user-action" for h in hosts):
            resetting = [
                h.get("requested_hostname") or h.get("id")
                for h in hosts
                if h.get("status") == "resetting-pending-user-action"
            ]
            print(
                f"  [{poll_num}] host(s) resetting — waiting for discovery ISO boot: "
                f"{', '.join(resetting)}"
            )
        else:
            ready, ready_hosts = all_hosts_oob_ready(
                ai, min_hosts=min_hosts, require_known=args.require_known, hosts=hosts
            )
            last_summary = _summarize_hosts(hosts)
            cluster_status = cluster.get("status") or ""
            if ready:
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
            print(
                f"  [{poll_num}] still waiting "
                f"(cluster={cluster_status!r} hosts={len(hosts)}; {last_summary})"
            )

        abort, reason = tracker.should_abort(
            max_action_streak=12 if any(
                (h.get("status") or "") == "resetting-pending-user-action" for h in hosts
            ) else 4
        )
        if abort:
            raise SystemExit(f"Stopping: {reason}\n{format_issues(issues)}")

        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {args.timeout}s waiting for {min_hosts} host(s) with OOB IPv4 "
                f"(last seen: {last_summary}).\n"
                f"{format_issues(issues + check_air_discovery_boot())}"
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
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

from __future__ import annotations

from dsx_air import air_status, kubeconfig, oc_checks, tunnel
from dsx_air.output import Report


def run_operators() -> int:
    report = Report()
    profile = air_status.profile_info()

    reachable, reason = tunnel.api_reachable()
    if not reachable:
        report.section("Operators")
        report.kv("status", f"API unreachable via tunnel ({reason})")
        report.warn("Start SSH tunnel (uv run dsx-air tunnel), then retry")
        return report.finish()

    try:
        kc = kubeconfig.ensure_tunneled_kubeconfig(cluster_name=profile["cluster_name"])
    except (FileNotFoundError, ValueError) as exc:
        report.section("Operators")
        report.kv("status", str(exc))
        report.warn(str(exc))
        return report.finish()

    report.section("Operators")
    _, rows, op_reason = oc_checks.operator_status(kubeconfig=str(kc))
    for row in rows:
        if row["label"] == "SriovNetworkNodePolicy":
            report.kv("SriovNetworkNodePolicy", row["csv"])
            report.kv("note", row["phase"])
            continue
        report.kv(
            row["label"],
            f"CSV {row['csv']} phase={row['phase']} pods={row['pods']}",
        )
    if op_reason:
        report.kv("detail", op_reason)

    return report.finish()

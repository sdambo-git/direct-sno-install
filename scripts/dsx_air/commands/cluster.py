from __future__ import annotations

from dsx_air import air_status, kubeconfig, oc_checks, tunnel
from dsx_air.output import Report


def run_cluster() -> int:
    report = Report()
    profile = air_status.profile_info()

    reachable, reason = tunnel.api_reachable()
    if not reachable:
        report.section("Cluster")
        report.kv("status", f"API unreachable via tunnel ({reason})")
        report.warn("Start SSH tunnel (uv run dsx-air tunnel), then retry")
        return report.finish()

    try:
        kc = kubeconfig.ensure_tunneled_kubeconfig(cluster_name=profile["cluster_name"])
    except (FileNotFoundError, ValueError) as exc:
        report.section("Cluster")
        report.kv("status", str(exc))
        report.warn(str(exc))
        return report.finish()

    ok, fields, cluster_reason = oc_checks.cluster_summary(kubeconfig=str(kc))
    report.section("Cluster")
    for key, value in fields.items():
        report.kv(key, value)
    version, _ = oc_checks.clusterversion(kubeconfig=str(kc))
    report.kv("cluster_version", version)
    mcp, _ = oc_checks.machineconfig_pools(kubeconfig=str(kc))
    if mcp:
        report.block(mcp)
    if not ok:
        report.warn(cluster_reason)

    return report.finish()

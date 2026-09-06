from __future__ import annotations

from pathlib import Path

from dsx_air import air_status, kubeconfig, oc_checks
from dsx_air.output import Report
from dsx_air.spec import activate_spec


def run_cluster(*, spec_path: Path | None = None) -> int:
    activate_spec(spec_path)
    report = Report()
    profile = air_status.profile_info()

    path, reason = kubeconfig.require_api(cluster_name=profile["cluster_name"])
    if path is None:
        report.section("Cluster")
        report.kv("status", f"API unreachable ({reason})")
        report.warn(
            "oc get --raw /version failed. Keep the ssh -L tunnel up and use "
            "the kubeconfig that already works for oc."
        )
        return report.finish()
    kc = str(path)

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

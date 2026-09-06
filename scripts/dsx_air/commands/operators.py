from __future__ import annotations

from pathlib import Path

from dsx_air import air_status, kubeconfig, oc_checks
from dsx_air.output import Report
from dsx_air.spec import activate_spec


def run_operators(*, spec_path: Path | None = None) -> int:
    activate_spec(spec_path)
    report = Report()
    profile = air_status.profile_info()

    path, reason = kubeconfig.require_api(cluster_name=profile["cluster_name"])
    if path is None:
        report.section("Operators")
        report.kv("status", f"API unreachable ({reason})")
        report.warn(
            "oc get --raw /version failed. Keep the ssh -L tunnel up and use "
            "the kubeconfig that already works for oc."
        )
        return report.finish()
    kc = str(path)

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

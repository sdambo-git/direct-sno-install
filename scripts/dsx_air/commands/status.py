from __future__ import annotations

from dsx_air import air_status, kubeconfig, oc_checks, tunnel
from dsx_air.air_status import AirLookupError
from dsx_air.output import Report


def _render_cluster_sections(
    report: Report,
    *,
    kubeconfig_path: str | None,
    check_operators: bool,
) -> None:
    if not kubeconfig_path:
        report.section("Cluster")
        report.kv("status", "skipped (no tunneled kubeconfig)")
        report.section("Operators")
        report.kv("status", "skipped (no tunneled kubeconfig)")
        return

    ok, fields, reason = oc_checks.cluster_summary(kubeconfig=kubeconfig_path)
    report.section("Cluster")
    for key, value in fields.items():
        report.kv(key, value)
    version, ver_reason = oc_checks.clusterversion(kubeconfig=kubeconfig_path)
    report.kv("cluster_version", version if version else ver_reason or "unknown")
    mcp, mcp_reason = oc_checks.machineconfig_pools(kubeconfig=kubeconfig_path)
    if mcp:
        report.block(mcp)
    elif mcp_reason:
        report.kv("machineconfigpool", mcp_reason)
    if not ok:
        report.warn(reason or "cluster not ready")

    if not check_operators:
        return

    report.section("Operators")
    _, rows, op_reason = oc_checks.operator_status(kubeconfig=kubeconfig_path)
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


def run_status(*, compact: bool = False) -> int:
    report = Report()

    profile = air_status.profile_info()
    report.section("Profile")
    report.kv("CLUSTER_PROFILE", profile["profile"])
    report.kv("cluster_name", profile["cluster_name"])
    report.kv("simulation_name", profile["simulation_name"])
    report.kv("api_vip", profile["api_vip"])

    sim_state = ""
    jump_ssh = ""
    try:
        sim = air_status.simulation_info()
        sim_state = sim["state"]
        report.section("Simulation")
        report.kv("name", sim["name"])
        report.kv("id", sim["id"])
        report.kv("state", sim_state)
        if sim_state != "ACTIVE":
            report.warn(
                f"Start simulation: uv run dsx-air start (current state: {sim_state})"
            )

        jump = air_status.jump_host_info()
        jump_ssh = jump["ssh"]
        report.section("Jump host")
        report.kv("ssh", jump_ssh or "(unavailable)")
        report.kv("ready", jump["ready"])
        if jump["ready"] != "yes":
            report.warn(f"Jump host not ready: {jump['reason']}")
    except AirLookupError as exc:
        report.section("Simulation")
        report.kv("status", f"skipped ({exc})")
        report.warn(str(exc))

    api_vip = profile["api_vip"]
    tunnel_cmd = tunnel.build_tunnel_command(jump_ssh=jump_ssh or "<jump-host-ssh>", api_vip=api_vip)
    report.section("Tunnel command")
    report.line(f"  {tunnel_cmd}")

    reachable, reach_reason = tunnel.api_reachable()
    report.section("API reachable")
    report.kv("via_tunnel", reachable and "yes" or f"no ({reach_reason})")
    if not reachable:
        report.warn(
            "Run the tunnel command in another terminal, then re-run status or demo"
        )

    kubeconfig_path: str | None = None
    if reachable:
        try:
            path = kubeconfig.ensure_tunneled_kubeconfig(
                cluster_name=profile["cluster_name"]
            )
            kubeconfig_path = str(path)
        except (FileNotFoundError, ValueError) as exc:
            report.warn(str(exc))

    if compact and kubeconfig_path:
        ok, _, reason = oc_checks.cluster_summary(kubeconfig=kubeconfig_path)
        if not ok:
            report.warn(reason or "cluster not ready")
    elif not compact:
        _render_cluster_sections(
            report,
            kubeconfig_path=kubeconfig_path,
            check_operators=True,
        )

    return report.finish(compact=compact)

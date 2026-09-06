from __future__ import annotations

import subprocess
from pathlib import Path

from dsx_air import air_status, kubeconfig, oc_checks, workload
from dsx_air.output import Report
from dsx_air.spec import activate_spec


def _need_api(report: Report, profile: dict[str, str]) -> str | None:
    path, reason = kubeconfig.require_api(cluster_name=profile["cluster_name"])
    if path is None:
        report.section("Workload")
        report.kv("status", f"API unreachable ({reason})")
        report.warn(
            "oc get --raw /version failed. Keep the ssh -L tunnel up and use "
            "the kubeconfig that already works for oc (api.<cluster>.<domain> "
            "in /etc/hosts)."
        )
        return None
    return str(path)


def _follow_logs(kubeconfig_path: str) -> int:
    cmd = [
        oc_checks.oc_path(),
        "--kubeconfig",
        kubeconfig_path,
        "logs",
        "-n",
        workload.NAMESPACE,
        "-l",
        "dsx-air/role=client",
        "--prefix",
        "--follow",
        "--max-log-requests=20",
        "--tail=30",
    ]
    print("Streaming client logs (Ctrl-C to stop; workload keeps running):", flush=True)
    print("+ " + " ".join(cmd), flush=True)
    try:
        proc = subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        return 0
    return int(proc.returncode or 0)


def run_workload(
    *,
    spec_path: Path | None = None,
    kind: str = "iperf",
    interval: int = 5,
    duration: int = 0,
    follow: bool = True,
    stop: bool = False,
    replace: bool = False,
) -> int:
    activate_spec(spec_path)
    report = Report()
    profile = air_status.profile_info()
    kc = _need_api(report, profile)
    if kc is None:
        return report.finish()

    if stop:
        result = workload.delete_namespace(kubeconfig=kc)
        report.section("Workload")
        if result.ok:
            report.kv("status", f"deleted namespace {workload.NAMESPACE}")
            return 0
        report.kv("status", result.reason)
        report.warn(result.reason)
        return report.finish()

    kind = kind.strip().lower()
    if kind not in {"iperf", "web"}:
        report.section("Workload")
        report.kv("status", f"unknown --kind {kind!r} (use iperf or web)")
        report.warn("Pass --kind iperf or --kind web")
        return report.finish()
    if interval < 1:
        interval = 1

    if follow and workload.client_pods_running(kubeconfig=kc):
        report.section("Workload")
        report.kv("namespace", workload.NAMESPACE)
        report.kv("status", "following existing client pods")
        report.stream.flush()
        return _follow_logs(kc)

    workers, err = workload.list_ready_workers(kubeconfig=kc)
    report.section("Workload")
    report.kv("kind", kind)
    report.kv("workers", ", ".join(workers) if workers else "(none)")
    if err:
        report.kv("status", err)
        report.warn(err)
        return report.finish()
    if len(workers) < 2:
        report.kv("status", "need at least 2 Ready dedicated worker nodes")
        report.warn(
            "Control-plane nodes are skipped. Scale workers or wait until "
            "ocp-worker-* are Ready."
        )
        return report.finish()

    pairs = workload.worker_pairs(workers)
    for src, dst in pairs:
        report.kv("stream", f"{src} -> {dst}")
    report.stream.flush()

    if replace:
        report.kv("status", f"replacing namespace {workload.NAMESPACE} (up to 3m)")
        report.stream.flush()
        deleted = workload.delete_namespace(kubeconfig=kc)
        if not deleted.ok:
            report.kv("status", deleted.reason)
            report.warn(deleted.reason)
            return report.finish()

    report.kv("status", f"applying pods ({workload.runtime_image()})")
    report.stream.flush()
    manifest = workload.render_manifest(
        workers, kind=kind, interval=interval, duration=duration
    )
    applied = workload.apply_manifest(kubeconfig=kc, manifest=manifest)
    if not applied.ok:
        report.kv("status", applied.reason)
        report.warn(applied.reason)
        return report.finish()
    if applied.stdout.strip():
        report.block(applied.stdout.strip())
        report.stream.flush()

    report.kv("status", "waiting for Ready")
    report.stream.flush()

    def _pods(snapshot: str) -> None:
        report.line()
        report.line("  pods:")
        report.block(snapshot)
        report.stream.flush()

    waited = workload.wait_ready(kubeconfig=kc, on_status=_pods)
    if not waited.ok:
        report.kv("status", waited.reason)
        report.warn(
            "Pods not Ready. If you see ImagePullBackOff, the cluster cannot "
            "pull that image. Default is registry.redhat.io/ubi9/ubi "
            "(override DSX_WORKLOAD_IMAGE). Re-run with --replace after a "
            f"failed docker.io pull. Check: oc get pods -n {workload.NAMESPACE}"
        )
        return report.finish()

    report.kv("namespace", workload.NAMESPACE)
    report.kv("interval_s", str(interval))
    if kind == "iperf":
        report.kv("iperf_duration_s", "forever" if duration == 0 else str(duration))
        report.kv("image", workload.runtime_image())
    else:
        report.kv("image", workload.runtime_image())
    report.line("  Stop: uv run dsx-air workload --stop")
    report.stream.flush()

    if follow:
        return _follow_logs(kc)
    report.line("  Logs: uv run dsx-air workload --follow")
    return 0

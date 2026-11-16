"""Health preflight for SNO/IBI target nodes reached through an Air jump host.

Failure mode this catches: Air suspends a simulation (auto-sleep) for more
than ~24h. Kubelet's client certificate is only valid for 24h and rotates
continuously while the node runs, but nothing rotates it while the VM is
suspended. When the sim wakes, kubelet presents the expired certificate,
the API server authenticates it as ``system:anonymous``, and kubelet cannot
register the node or start workload pods. Only the static control-plane
pods come back, so the API (6443, static pod) answers while the router
(443, workload pod) refuses connections — tunnels come up fine, but the
web console and ``oc`` fail in confusing ways.

The fix is to approve the pending kubelet CSRs as kubeadmin; kubelet then
re-issues its client certificate and the cluster self-heals within
minutes. ``run_preflight`` probes the node over SSH (via the jump host)
and, when it recognises the failure, performs that fix automatically.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from dsx_air._bootstrap import repo_root
from dsx_air.tunnel import JumpTarget

NODE_SSH_USER = "core"
KUBELET_CLIENT_CERT = "/var/lib/kubelet/pki/kubelet-client-current.pem"
KUBELET_CSR_SIGNER = "kubernetes.io/kube-apiserver-client-kubelet"
# etcd, kube-apiserver, kube-scheduler, kube-controller-manager,
# kube-rbac-proxy-crio — plus slack. A healthy SNO runs dozens of pods;
# a node whose kubelet cannot authenticate runs only these.
STATIC_POD_CEILING = 8

# After approving the CSRs, how long to keep polling for recovery, how
# often, and how long to give kubelet's own (backoff-capped) retry before
# restarting it once so it claims the signed certificate immediately.
RECOVERY_TIMEOUT_SECONDS = 360.0
RECOVERY_POLL_SECONDS = 20.0
KUBELET_RESTART_AFTER = 120.0

# SSH options for probes: never let a stale laptop known_hosts entry (Air
# reuses 192.168.200.x across sim re-imports, and host keys change across
# node rebuilds/reboots) block the check.
_PROBE_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "ConnectTimeout=10",
]


@dataclass(frozen=True)
class NodeReport:
    """Facts gathered from the target node in one SSH round trip."""

    ssh_ok: bool
    cert_end: str | None = None  # notAfter of the kubelet client cert
    cert_expired: bool | None = None  # None = unknown / not present
    pod_count: int | None = None
    ssh_error: str | None = None

    @property
    def needs_fix(self) -> bool:
        if self.cert_expired is True:
            return True
        return self.pod_count is not None and self.pod_count <= STATIC_POD_CEILING


@dataclass(frozen=True)
class PreflightResult:
    report: NodeReport
    attempted_fix: bool = False
    approved: tuple[str, ...] = ()
    fix_error: str | None = None
    kubeconfig: Path | None = None
    restarted: bool = False
    messages: tuple[str, ...] = field(default=())

    @property
    def fixed(self) -> bool:
        return bool(self.approved)

    @property
    def recovered(self) -> bool:
        """True when a fix was applied and the node came back healthy."""
        return self.fixed and self.report.ssh_ok and not self.report.needs_fix


def parse_node_report(text: str) -> dict[str, str]:
    """Parse the KEY=VALUE lines printed by the probe script."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def node_report_from_output(text: str) -> NodeReport:
    """Build a NodeReport from the probe script's stdout."""
    values = parse_node_report(text)
    expired_raw = values.get("EXPIRED")
    pods_raw = values.get("PODS")
    return NodeReport(
        ssh_ok=True,
        cert_end=values.get("ENDDATE") or None,
        cert_expired=None if expired_raw not in {"yes", "no"} else expired_raw == "yes",
        pod_count=int(pods_raw) if pods_raw and pods_raw.isdigit() else None,
    )


def probe_node(jump: JumpTarget, node_ip: str, *, timeout: int = 30) -> NodeReport:
    """SSH to the target node via the jump host and gather health facts."""
    remote = (
        f"CERT={KUBELET_CLIENT_CERT}\n"
        "if sudo test -f $CERT; then\n"
        "  ENDDATE=$(sudo openssl x509 -in $CERT -noout -enddate 2>/dev/null | cut -d= -f2)\n"
        "  if sudo openssl x509 -in $CERT -checkend 0 -noout >/dev/null 2>&1; then EXPIRED=no; else EXPIRED=yes; fi\n"
        "else\n"
        "  ENDDATE=none\n"
        "  EXPIRED=unknown\n"
        "fi\n"
        'PODS=$(sudo crictl pods -q 2>/dev/null | wc -l)\n'
        'echo "ENDDATE=$ENDDATE"\n'
        'echo "EXPIRED=$EXPIRED"\n'
        'echo "PODS=$PODS"\n'
    )
    cmd = [
        "ssh",
        *_PROBE_SSH_OPTS,
        "-o",
        f"ProxyJump={jump.username}@{jump.host}:{jump.port}",
        f"{NODE_SSH_USER}@{node_ip}",
        remote,
    ]
    try:
        run = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return NodeReport(ssh_ok=False, ssh_error=str(exc))
    if run.returncode != 0:
        reason = (run.stderr or "").strip().splitlines()[-1:] or ["ssh failed"]
        return NodeReport(ssh_ok=False, ssh_error=reason[0])
    return node_report_from_output(run.stdout)


def pending_kubelet_csrs(csr_json: dict) -> list[str]:
    """Names of still-pending CSRs for the kubelet client cert signer."""
    names: list[str] = []
    for item in csr_json.get("items", []):
        spec = item.get("spec") or {}
        if spec.get("signerName") != KUBELET_CSR_SIGNER:
            continue
        status = item.get("status") or {}
        if not (status.get("conditions") or []):
            name = (item.get("metadata") or {}).get("name")
            if name:
                names.append(name)
    return names


def admin_kubeconfig(cluster_name: str | None) -> Path | None:
    """First existing kubeadmin kubeconfig for the target cluster.

    IBI targets keep theirs in ``ibi-config-iso-workdir/auth/``; Assisted
    clusters download it to ``.cache/kubeconfig.<cluster>``.
    """
    from dsx_air.kubeconfig import kubeconfig_path

    candidates = [repo_root() / "ibi-config-iso-workdir" / "auth" / "kubeconfig"]
    if cluster_name:
        candidates.append(kubeconfig_path(cluster_name=cluster_name))
    for path in candidates:
        if path.is_file():
            return path
    return None


@contextlib.contextmanager
def temp_api_forward(jump: JumpTarget, node_ip: str, *, timeout: float = 20.0) -> Iterator[int]:
    """Temporarily forward a free local port to the node's API server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cmd = [
        "ssh",
        "-N",
        *_PROBE_SSH_OPTS,
        "-o",
        "ExitOnForwardFailure=yes",
        "-L",
        f"127.0.0.1:{port}:{node_ip}:6443",
        "-p",
        str(jump.port),
        f"{jump.username}@{jump.host}",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if proc.poll() is not None:
                raise RuntimeError("ssh API forward exited before listening")
            time.sleep(0.3)
        else:
            raise RuntimeError("timed out waiting for the local API forward")
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _oc(kubeconfig: Path, server: str, *args: str, timeout: float = 90.0):
    return subprocess.run(
        [
            "oc",
            "--kubeconfig",
            str(kubeconfig),
            "--server",
            server,
            "--insecure-skip-tls-verify",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def approve_kubelet_csrs(
    jump: JumpTarget, node_ip: str, kubeconfig: Path
) -> tuple[list[str], str | None]:
    """Approve pending kubelet CSRs through a temporary API forward.

    Returns ``(approved_names, error)`` — exactly one of them is set.
    """
    if shutil.which("oc") is None:
        return [], "`oc` is not on PATH"
    try:
        with temp_api_forward(jump, node_ip) as port:
            server = f"https://127.0.0.1:{port}"
            listed = _oc(kubeconfig, server, "get", "csr", "-o", "json")
            if listed.returncode != 0:
                return [], f"oc get csr failed: {(listed.stderr or '').strip()}"
            names = pending_kubelet_csrs(json.loads(listed.stdout or "{}"))
            if not names:
                return [], None
            approved = _oc(kubeconfig, server, "adm", "certificate", "approve", *names)
            if approved.returncode != 0:
                return [], f"oc adm certificate approve failed: {(approved.stderr or '').strip()}"
            return names, None
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return [], str(exc)


def restart_kubelet(jump: JumpTarget, node_ip: str, *, timeout: int = 30) -> bool:
    """Restart kubelet on the node (clears certificate-retry backoff)."""
    cmd = [
        "ssh",
        *_PROBE_SSH_OPTS,
        "-o",
        f"ProxyJump={jump.username}@{jump.host}:{jump.port}",
        f"{NODE_SSH_USER}@{node_ip}",
        "sudo systemctl restart kubelet",
    ]
    try:
        run = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return run.returncode == 0


def wait_for_recovery(
    jump: JumpTarget,
    node_ip: str,
    *,
    timeout: float = RECOVERY_TIMEOUT_SECONDS,
    poll: float = RECOVERY_POLL_SECONDS,
    restart_after: float = KUBELET_RESTART_AFTER,
    progress=None,
) -> tuple[NodeReport, bool]:
    """Poll the node until it is healthy again.

    Kubelet's certificate manager retries with a capped backoff, which can
    leave an approved CSR unclaimed for a long time; once ``restart_after``
    seconds have passed with the certificate still expired, kubelet is
    restarted once so it retries immediately. ``restart_after <= 0``
    disables the restart. Returns ``(final_report, kubelet_restarted)``.
    """
    start = time.monotonic()
    restarted = False
    while True:
        report = probe_node(jump, node_ip)
        if not report.ssh_ok or not report.needs_fix:
            return report, restarted
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            return report, restarted
        if (
            report.cert_expired
            and not restarted
            and restart_after
            and elapsed >= restart_after
        ):
            restarted = restart_kubelet(jump, node_ip)
        if progress is not None:
            try:
                progress(report, elapsed)
            except Exception:  # progress printing must never break recovery
                pass
        time.sleep(poll)


def progress_line(report: NodeReport, elapsed: float) -> str:
    """One status line for the recovery wait, printable by callers."""
    pods = report.pod_count if report.pod_count is not None else "?"
    return (
        f"preflight: waiting for node recovery ({int(elapsed)}s elapsed, "
        f"{pods} pods running) ..."
    )


def _messages(
    node_ip: str,
    report: NodeReport,
    approved: tuple[str, ...],
    fix_error: str | None,
    kubeconfig: Path | None,
    *,
    restarted: bool = False,
) -> tuple[str, ...]:
    lines: list[str] = []
    if not report.ssh_ok:
        lines.append(
            f"preflight: could not SSH to node {node_ip} via jump host "
            f"({report.ssh_error}); skipping node health check"
        )
        return tuple(lines)

    problems: list[str] = []
    if report.cert_expired is True:
        problems.append(
            f"kubelet client certificate EXPIRED (notAfter={report.cert_end})"
        )
    if report.pod_count is not None and report.pod_count <= STATIC_POD_CEILING:
        problems.append(
            f"only {report.pod_count} pod(s) running (workload pods missing — "
            "router/console never started)"
        )
    if not problems:
        cert = (
            "valid"
            if report.cert_expired is False
            else f"notAfter={report.cert_end or 'unknown'}"
        )
        pods = f"{report.pod_count} pods running" if report.pod_count is not None else "pod count unknown"
        if approved:
            lines.append(
                f"preflight: approved {len(approved)} pending kubelet CSR(s): "
                + ", ".join(approved)
            )
            if restarted:
                lines.append(
                    "preflight: restarted kubelet to clear its certificate-retry backoff"
                )
            lines.append(
                f"preflight: node {node_ip} recovered (kubelet cert {cert}, {pods}) "
                "— continuing"
            )
        else:
            lines.append(f"preflight: node {node_ip} healthy (kubelet cert {cert}, {pods})")
        return tuple(lines)

    lines.append(f"preflight: node {node_ip} problem: {'; '.join(problems)}")
    if approved:
        lines.append(
            f"preflight: approved {len(approved)} pending kubelet CSR(s): "
            + ", ".join(approved)
        )
        if restarted:
            lines.append(
                "preflight: restarted kubelet to clear its certificate-retry backoff"
            )
        lines.append(
            "preflight: node still recovering — workload pods (router/console) take "
            "a few minutes to come back; re-run this command shortly"
        )
    elif fix_error:
        lines.append(f"preflight: auto-fix failed: {fix_error}")
    else:
        lines.append("preflight: no pending kubelet CSRs found to approve")
        lines.append(
            "preflight: if the node stays broken, investigate with: "
            f"ssh -o ProxyJump=<jump-host> {NODE_SSH_USER}@{node_ip} "
            "'journalctl -u kubelet -b | tail -50'"
        )
    if not approved and kubeconfig is not None:
        lines.append(
            "preflight: manual fix — with a tunnel running on 6443:\n"
            f"  oc --kubeconfig {kubeconfig} --server https://127.0.0.1:6443 "
            "--insecure-skip-tls-verify get csr\n"
            "  oc --kubeconfig " + str(kubeconfig) + " --server https://127.0.0.1:6443 "
            "--insecure-skip-tls-verify adm certificate approve <pending names>"
        )
    return tuple(lines)


def run_preflight(
    jump: JumpTarget,
    node_ip: str,
    *,
    cluster_name: str | None = None,
    auto_fix: bool = True,
    wait_recovery: bool = True,
    timeout: float = RECOVERY_TIMEOUT_SECONDS,
    progress=None,
) -> PreflightResult:
    """Probe the target node; approve pending kubelet CSRs and wait for recovery.

    When the node is broken and the CSRs were approved, keep polling until
    the node is healthy again (restarting kubelet once if its certificate
    retry backoff keeps it from claiming the signed certificate), so the
    caller can proceed without a manual re-run.
    """
    report = probe_node(jump, node_ip)
    if not report.ssh_ok or not report.needs_fix or not auto_fix:
        return PreflightResult(
            report=report,
            messages=_messages(node_ip, report, (), None, None),
        )

    kubeconfig = admin_kubeconfig(cluster_name)
    if kubeconfig is None:
        return PreflightResult(
            report=report,
            kubeconfig=None,
            messages=_messages(
                node_ip,
                report,
                (),
                "no kubeadmin kubeconfig found (neither ibi-config-iso-workdir/auth/ "
                "nor .cache/kubeconfig.*)",
                None,
            ),
        )

    approved, fix_error = approve_kubelet_csrs(jump, node_ip, kubeconfig)
    restarted = False
    final_report = report
    if approved and not fix_error and wait_recovery:
        final_report, restarted = wait_for_recovery(
            jump, node_ip, timeout=timeout, progress=progress
        )
    return PreflightResult(
        report=final_report,
        attempted_fix=True,
        approved=tuple(approved),
        fix_error=fix_error,
        kubeconfig=kubeconfig,
        restarted=restarted,
        messages=_messages(
            node_ip,
            final_report,
            tuple(approved),
            fix_error,
            kubeconfig,
            restarted=restarted,
        ),
    )

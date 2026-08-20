from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from dsx_air._bootstrap import ensure_scripts_path

ensure_scripts_path()

import env_config  # noqa: E402

OPERATORS = (
    ("NFD", "openshift-nfd", "nfd"),
    ("NMState", "openshift-nmstate", "kubernetes-nmstate-operator"),
    ("SR-IOV", "openshift-sriov-network-operator", "sriov-network-operator"),
)


@dataclass
class OcResult:
    ok: bool
    stdout: str
    stderr: str
    reason: str = ""


def oc_path() -> str:
    path = shutil.which("oc")
    if not path:
        raise FileNotFoundError(
            "oc not found in PATH. Install the OpenShift CLI and retry."
        )
    return path


def run_oc(
    args: list[str],
    *,
    kubeconfig: str | None = None,
    timeout: float = 60.0,
) -> OcResult:
    cmd = [oc_path()]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return OcResult(ok=False, stdout="", stderr="", reason="oc command timed out")
    except FileNotFoundError as exc:
        return OcResult(ok=False, stdout="", stderr="", reason=str(exc))

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return OcResult(
            ok=False,
            stdout=proc.stdout,
            stderr=proc.stderr,
            reason=detail or f"oc exit {proc.returncode}",
        )
    return OcResult(ok=True, stdout=proc.stdout, stderr=proc.stderr)


def cluster_summary(*, kubeconfig: str) -> tuple[bool, dict[str, str], str]:
    """Return (ok, fields, reason). ok when 3 nodes Ready (multinode default)."""
    nodes = run_oc(["get", "nodes", "-o", "json"], kubeconfig=kubeconfig)
    if not nodes.ok:
        return False, {}, nodes.reason

    data = json.loads(nodes.stdout)
    items = data.get("items", [])
    ready = 0
    for item in items:
        for cond in item.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                ready += 1
                break

    expected = env_config.expected_hosts()
    fields = {
        "nodes_total": str(len(items)),
        "nodes_ready": str(ready),
        "expected_ready": str(expected),
    }
    if ready < expected or len(items) < expected:
        return (
            False,
            fields,
            f"expected {expected} Ready nodes, saw {ready}/{len(items)}",
        )
    return True, fields, ""


def clusterversion(*, kubeconfig: str) -> tuple[str, str]:
    result = run_oc(
        ["get", "clusterversion", "version", "-o", "jsonpath={.status.desired.version}"],
        kubeconfig=kubeconfig,
    )
    if result.ok and result.stdout.strip():
        return result.stdout.strip(), ""
    fallback = run_oc(["get", "clusterversion"], kubeconfig=kubeconfig)
    if fallback.ok:
        return fallback.stdout.strip().splitlines()[1] if "\n" in fallback.stdout else "unknown", ""
    return "unknown", result.reason or fallback.reason


def machineconfig_pools(*, kubeconfig: str) -> tuple[str, str]:
    result = run_oc(["get", "machineconfigpool"], kubeconfig=kubeconfig)
    if result.ok:
        return result.stdout.strip(), ""
    return "", result.reason


def operator_status(*, kubeconfig: str) -> tuple[bool, list[dict[str, str]], str]:
    rows: list[dict[str, str]] = []
    any_failed = False
    last_reason = ""

    for label, namespace, name_hint in OPERATORS:
        csv = run_oc(
            [
                "get",
                "csv",
                "-n",
                namespace,
                "-o",
                "json",
            ],
            kubeconfig=kubeconfig,
            timeout=90.0,
        )
        phase = "missing"
        csv_name = ""
        if csv.ok:
            payload = json.loads(csv.stdout)
            for item in payload.get("items", []):
                if name_hint in item.get("metadata", {}).get("name", ""):
                    csv_name = item["metadata"]["name"]
                    phase = (
                        item.get("status", {})
                        .get("phase", "Unknown")
                    )
                    break
            if not csv_name and payload.get("items"):
                item = payload["items"][0]
                csv_name = item["metadata"]["name"]
                phase = item.get("status", {}).get("phase", "Unknown")
        else:
            any_failed = True
            last_reason = csv.reason
            phase = "unreachable"

        pods = run_oc(
            ["get", "pods", "-n", namespace, "--no-headers"],
            kubeconfig=kubeconfig,
        )
        pod_summary = "unknown"
        if pods.ok:
            lines = [line for line in pods.stdout.splitlines() if line.strip()]
            running = 0
            for line in lines:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "Running":
                    running += 1
            pod_summary = f"{running}/{len(lines)} Running" if lines else "0 pods"

        rows.append(
            {
                "label": label,
                "namespace": namespace,
                "csv": csv_name or "(none)",
                "phase": phase,
                "pods": pod_summary,
            }
        )
        if phase not in {"Succeeded", "InstallReady"}:
            any_failed = True

    sriov_policies = run_oc(
        ["get", "sriovnetworknodepolicy", "-A", "--no-headers"],
        kubeconfig=kubeconfig,
    )
    policy_count = "0"
    if sriov_policies.ok:
        lines = [line for line in sriov_policies.stdout.splitlines() if line.strip()]
        policy_count = str(len(lines))

    rows.append(
        {
            "label": "SriovNetworkNodePolicy",
            "namespace": "(cluster)",
            "csv": policy_count,
            "phase": "expected 0 without SR-IOV NICs",
            "pods": "",
        }
    )

    return not any_failed, rows, last_reason

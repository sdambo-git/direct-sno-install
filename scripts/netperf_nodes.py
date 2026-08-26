#!/usr/bin/env python3
"""
Run iperf3 between pods scheduled on different cluster nodes.

Pins one iperf3 server pod per Ready node (tolerates control-plane taints so
HA masters that also have the worker role can run the workload). Then runs a
TCP full-mesh from each pod to the others over the pod network.

Needs a working kubeconfig (API tunnel on this laptop, or oc on the jump host):

    export KUBECONFIG=../.cache/kubeconfig.ocp-cluster
    uv run netperf_nodes.py
    uv run netperf_nodes.py --duration 20 --keep
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import env_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_IMAGE = os.environ.get("IPERF_IMAGE", "docker.io/networkstatic/iperf3:latest")
_DEFAULT_NS = "dsxair-netperf"


def _kubeconfig() -> Path:
    env = os.environ.get("KUBECONFIG")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / ".cache" / f"kubeconfig.{env_config.cluster_name()}"


def _oc_bin() -> Path:
    cached = _REPO_ROOT / ".cache" / "oc-client" / "oc"
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached
    on_path = shutil.which("oc")
    if on_path:
        return Path(on_path)
    raise SystemExit("oc not found. Run step 11 or put oc on PATH.")


def _oc_env(kubeconfig: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["KUBECONFIG"] = str(kubeconfig)
    return env


def _oc(oc: Path, env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(oc), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _ready_nodes(oc: Path, env: dict[str, str]) -> list[str]:
    listing = _oc(oc, env, "get", "nodes", "-o", "json")
    items = json.loads(listing.stdout).get("items") or []
    names: list[str] = []
    for node in items:
        name = node.get("metadata", {}).get("name")
        conds = node.get("status", {}).get("conditions") or []
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True" for c in conds
        )
        if name and ready:
            names.append(name)
    return names


def _pod_name(node: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in node).strip("-")
    return f"iperf-{safe[:40]}"


def _manifest(namespace: str, nodes: list[str], image: str) -> str:
    docs: list[str] = [
        f"""apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    app: dsxair-netperf
"""
    ]
    for node in nodes:
        name = _pod_name(node)
        docs.append(
            f"""apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app: dsxair-iperf
    dsxair-node: {node}
spec:
  restartPolicy: Always
  nodeSelector:
    kubernetes.io/hostname: {node}
  tolerations:
    - operator: Exists
  containers:
    - name: iperf3
      image: {image}
      imagePullPolicy: IfNotPresent
      command: ["iperf3", "-s", "-p", "5201"]
      ports:
        - containerPort: 5201
          protocol: TCP
"""
        )
    return "---\n".join(docs)


def _wait_pods(oc: Path, env: dict[str, str], namespace: str, nodes: list[str], timeout: int) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    names = [_pod_name(n) for n in nodes]
    while time.monotonic() < deadline:
        listing = _oc(oc, env, "get", "pods", "-n", namespace, "-o", "json", check=False)
        if listing.returncode != 0:
            time.sleep(5)
            continue
        pods = {p["metadata"]["name"]: p for p in json.loads(listing.stdout).get("items") or []}
        ips: dict[str, str] = {}
        waiting: list[str] = []
        for node, name in zip(nodes, names):
            pod = pods.get(name)
            if not pod:
                waiting.append(f"{name}=missing")
                continue
            phase = pod.get("status", {}).get("phase")
            ip = pod.get("status", {}).get("podIP")
            ready = False
            for cs in pod.get("status", {}).get("containerStatuses") or []:
                if cs.get("ready"):
                    ready = True
                waiting_state = (cs.get("state") or {}).get("waiting") or {}
                reason = waiting_state.get("reason")
                if reason:
                    waiting.append(f"{name}={reason}")
            if phase == "Running" and ready and ip:
                ips[node] = ip
            elif name not in "".join(waiting):
                waiting.append(f"{name}={phase}")
        if len(ips) == len(nodes):
            return ips
        print("  waiting for iperf pods: " + (", ".join(waiting) or "creating"))
        time.sleep(8)
    raise SystemExit(
        f"Timed out after {timeout}s waiting for iperf pods in {namespace}. "
        f"Check: oc get pods -n {namespace}"
    )


def _run_iperf(
    oc: Path,
    env: dict[str, str],
    namespace: str,
    src_node: str,
    dst_ip: str,
    *,
    duration: int,
    parallel: int,
) -> float:
    src = _pod_name(src_node)
    result = _oc(
        oc,
        env,
        "exec",
        "-n",
        namespace,
        src,
        "--",
        "iperf3",
        "-c",
        dst_ip,
        "-p",
        "5201",
        "-t",
        str(duration),
        "-P",
        str(parallel),
        "-J",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(f"iperf3 {src_node} -> {dst_ip} failed: {detail}")
    payload = json.loads(result.stdout)
    received = (payload.get("end") or {}).get("sum_received") or {}
    bps = received.get("bits_per_second")
    if bps is None:
        sent = (payload.get("end") or {}).get("sum_sent") or {}
        bps = sent.get("bits_per_second")
    if bps is None:
        raise SystemExit(f"iperf3 {src_node} -> {dst_ip} returned no bits_per_second")
    return float(bps)


def _mbits(bps: float) -> str:
    return f"{bps / 1_000_000:.1f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default=_DEFAULT_NS)
    parser.add_argument("--image", default=_DEFAULT_IMAGE)
    parser.add_argument("--duration", type=int, default=10, help="iperf3 seconds per pair")
    parser.add_argument("--parallel", type=int, default=4, help="iperf3 parallel streams")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for pods")
    parser.add_argument("--keep", action="store_true", help="Leave the namespace after the test")
    args = parser.parse_args()

    kubeconfig = _kubeconfig()
    if not kubeconfig.is_file():
        raise SystemExit(f"Kubeconfig not found: {kubeconfig}")
    oc = _oc_bin()
    env = _oc_env(kubeconfig)

    probe = _oc(oc, env, "get", "nodes", check=False)
    if probe.returncode != 0:
        raise SystemExit(
            "oc cannot reach the API. Start the API VIP tunnel, then:\n"
            f"  export KUBECONFIG={kubeconfig}\n"
            f"  oc get nodes\n"
            f"Detail: {(probe.stderr or probe.stdout or '').strip()}"
        )

    nodes = _ready_nodes(oc, env)
    if len(nodes) < 2:
        raise SystemExit(
            f"Need at least two Ready nodes for inter-host iperf (found {nodes})."
        )
    print(f"Nodes: {', '.join(nodes)}")
    print(f"Creating iperf3 pods in namespace {args.namespace} (image {args.image})")

    applied = subprocess.run(
        [str(oc), "apply", "-f", "-"],
        env=env,
        input=_manifest(args.namespace, nodes, args.image),
        text=True,
        capture_output=True,
        check=False,
    )
    if applied.returncode != 0:
        raise SystemExit((applied.stderr or applied.stdout or "").strip())
    scc = _oc(
        oc,
        env,
        "adm",
        "policy",
        "add-scc-to-user",
        "anyuid",
        "-z",
        "default",
        "-n",
        args.namespace,
        check=False,
    )
    if scc.returncode != 0:
        print(f"  note: anyuid SCC not applied ({(scc.stderr or '').strip()})")

    ips = _wait_pods(oc, env, args.namespace, nodes, args.timeout)
    for node, ip in ips.items():
        print(f"  {node}: {ip} ({_pod_name(node)})")

    print(f"\nTCP iperf3 full mesh ({args.duration}s, -P {args.parallel}):\n")
    print(f"{'src':<16} {'dst':<16} {'Mbits/s':>10}")
    rows: list[tuple[str, str, float]] = []
    try:
        for src in nodes:
            for dst in nodes:
                if src == dst:
                    continue
                bps = _run_iperf(
                    oc,
                    env,
                    args.namespace,
                    src,
                    ips[dst],
                    duration=args.duration,
                    parallel=args.parallel,
                )
                rows.append((src, dst, bps))
                print(f"{src:<16} {dst:<16} {_mbits(bps):>10}")
    finally:
        if not args.keep:
            print(f"\nDeleting namespace {args.namespace}")
            _oc(oc, env, "delete", "namespace", args.namespace, "--wait=false", check=False)
        else:
            print(f"\nKept namespace {args.namespace} (--keep)")

    if rows:
        avg = sum(r[2] for r in rows) / len(rows)
        print(f"\nAverage: {_mbits(avg)} Mbits/s across {len(rows)} pairs")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

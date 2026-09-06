"""Worker-to-worker lab traffic: iperf3 ring or HTTP client/server."""
from __future__ import annotations

import json
import time
import os
from typing import Any, Callable

import yaml

from dsx_air.oc_checks import OcResult, run_oc

NAMESPACE = "dsx-air-workload"

# OpenShift pull secret covers registry.redhat.io; docker.io is often blocked on Air.
DEFAULT_RUNTIME_IMAGE = "registry.redhat.io/ubi9/ubi:9.6"

TCP_SERVER = """
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 5201))
s.listen(8)
while True:
    c, _ = s.accept()
    try:
        while c.recv(256 * 1024):
            pass
    except OSError:
        pass
    finally:
        c.close()
"""

TCP_CLIENT = """
import os, socket, time
host = os.environ["TARGET"]
interval = max(1, int(os.environ.get("INTERVAL", "5")))
payload = b"x" * (64 * 1024)
while True:
    try:
        s = socket.create_connection((host, 5201), timeout=15)
        n = 0
        last = time.monotonic()
        lastn = 0
        while True:
            s.sendall(payload)
            n += len(payload)
            now = time.monotonic()
            if now - last >= interval:
                mbps = 8 * (n - lastn) / (now - last) / 1e6
                print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {host}: {mbps:.1f} Mbits/sec", flush=True)
                last, lastn = now, n
    except Exception as exc:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} retry {host}: {exc}", flush=True)
        time.sleep(2)
"""

HTTP_CLIENT = """
import os, time, urllib.error, urllib.request
url = os.environ["TARGET"]
interval = max(1, int(os.environ.get("INTERVAL", "5")))
while True:
    t0 = time.monotonic()
    code = "err"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            code = str(resp.status)
    except urllib.error.HTTPError as exc:
        code = str(exc.code)
    except Exception as exc:
        code = str(exc)
    dt = time.monotonic() - t0
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {url} code={code} time={dt:.3f}s", flush=True)
    time.sleep(interval)
"""


def runtime_image() -> str:
    return (
        os.environ.get("DSX_WORKLOAD_IMAGE")
        or os.environ.get("DSX_IPERF_IMAGE")
        or DEFAULT_RUNTIME_IMAGE
    ).strip() or DEFAULT_RUNTIME_IMAGE


def iperf_image() -> str:
    return runtime_image()


def web_image() -> str:
    return runtime_image()


def curl_image() -> str:
    return runtime_image()


def is_dedicated_worker(labels: dict[str, Any]) -> bool:
    if "node-role.kubernetes.io/control-plane" in labels:
        return False
    if "node-role.kubernetes.io/master" in labels:
        return False
    return "node-role.kubernetes.io/worker" in labels


def node_is_ready(item: dict[str, Any]) -> bool:
    for cond in item.get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def dedicated_ready_workers(items: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in items:
        meta = item.get("metadata", {})
        labels = meta.get("labels") or {}
        name = meta.get("name")
        if not name:
            continue
        if is_dedicated_worker(labels) and node_is_ready(item):
            names.append(str(name))
    return sorted(names)


def worker_pairs(names: list[str]) -> list[tuple[str, str]]:
    """Ring: each worker is a client of the next (wraps around).

    Two workers → both directions. Three workers → 0→1, 1→2, 2→0.
    """
    if len(names) < 2:
        return []
    return [(names[i], names[(i + 1) % len(names)]) for i in range(len(names))]


def _meta(name: str, *, role: str, index: int, node: str) -> dict[str, Any]:
    return {
        "name": name,
        "namespace": NAMESPACE,
        "labels": {
            "app.kubernetes.io/part-of": "dsx-air",
            "dsx-air/role": role,
            "dsx-air/index": str(index),
            "dsx-air/node": node,
        },
    }


def _pod_template(
    *,
    name: str,
    role: str,
    index: int,
    node: str,
    image: str,
    args: list[str],
    ports: list[dict[str, Any]] | None,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": role,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "2", "memory": "256Mi"},
        },
    }
    if command:
        container["command"] = command
    if args:
        container["args"] = args
    if ports:
        container["ports"] = ports
    if env:
        container["env"] = [{"name": key, "value": value} for key, value in env.items()]
    return {
        "metadata": _meta(name, role=role, index=index, node=node),
        "spec": {
            "restartPolicy": "Always",
            "nodeSelector": {"kubernetes.io/hostname": node},
            "containers": [container],
        },
    }


def _deployment(template: dict[str, Any]) -> dict[str, Any]:
    labels = template["metadata"]["labels"]
    spec = dict(template["spec"])
    spec["restartPolicy"] = "Always"
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": template["metadata"],
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "strategy": {"type": "Recreate"},
            "template": {
                "metadata": {"labels": labels},
                "spec": spec,
            },
        },
    }


def _service(*, name: str, role: str, index: int, node: str, port: int) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(name, role=role, index=index, node=node),
        "spec": {
            "selector": {
                "dsx-air/role": "server",
                "dsx-air/index": str(index),
            },
            "ports": [{"name": "app", "port": port, "targetPort": port}],
        },
    }


def _namespace_docs() -> list[dict[str, Any]]:
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {"app.kubernetes.io/part-of": "dsx-air"},
            },
        },
    ]


def render_manifest(
    workers: list[str],
    *,
    kind: str,
    interval: int,
    duration: int,
) -> str:
    if kind not in {"iperf", "web"}:
        raise ValueError(f"kind must be iperf or web, not {kind!r}")
    docs = list(_namespace_docs())
    pairs = worker_pairs(workers)
    index_of = {name: i for i, name in enumerate(workers)}
    image = runtime_image()
    if kind == "iperf":
        port = 5201
        for i, node in enumerate(workers):
            docs.append(
                _deployment(
                    _pod_template(
                        name=f"iperf-server-{i}",
                        role="server",
                        index=i,
                        node=node,
                        image=image,
                        command=["python3", "-c", TCP_SERVER],
                        args=[],
                        ports=[{"containerPort": port, "name": "iperf"}],
                    )
                )
            )
            docs.append(
                _service(
                    name=f"iperf-{i}",
                    role="svc",
                    index=i,
                    node=node,
                    port=port,
                )
            )
        for i, (client_node, server_node) in enumerate(pairs):
            target = f"iperf-{index_of[server_node]}"
            docs.append(
                _deployment(
                    _pod_template(
                        name=f"iperf-client-{i}",
                        role="client",
                        index=i,
                        node=client_node,
                        image=image,
                        command=["python3", "-c", TCP_CLIENT],
                        args=[],
                        ports=None,
                        env={"TARGET": target, "INTERVAL": str(interval)},
                    )
                )
            )
    else:
        port = 8080
        for i, node in enumerate(workers):
            docs.append(
                _deployment(
                    _pod_template(
                        name=f"web-server-{i}",
                        role="server",
                        index=i,
                        node=node,
                        image=image,
                        command=["python3", "-m", "http.server", str(port), "--bind", "0.0.0.0"],
                        args=[],
                        ports=[{"containerPort": port, "name": "http"}],
                    )
                )
            )
            docs.append(
                _service(
                    name=f"web-{i}",
                    role="svc",
                    index=i,
                    node=node,
                    port=port,
                )
            )
        for i, (client_node, server_node) in enumerate(pairs):
            target = f"http://web-{index_of[server_node]}:{port}/"
            docs.append(
                _deployment(
                    _pod_template(
                        name=f"web-client-{i}",
                        role="client",
                        index=i,
                        node=client_node,
                        image=image,
                        command=["python3", "-c", HTTP_CLIENT],
                        args=[],
                        ports=None,
                        env={"TARGET": target, "INTERVAL": str(interval)},
                    )
                )
            )
    return yaml.dump_all(docs, default_flow_style=False, sort_keys=False)


def list_ready_workers(*, kubeconfig: str) -> tuple[list[str], str]:
    result = run_oc(["get", "nodes", "-o", "json"], kubeconfig=kubeconfig)
    if not result.ok:
        return [], result.reason
    payload = json.loads(result.stdout)
    return dedicated_ready_workers(payload.get("items") or []), ""


def apply_manifest(*, kubeconfig: str, manifest: str) -> OcResult:
    return run_oc(
        ["apply", "-f", "-"],
        kubeconfig=kubeconfig,
        stdin=manifest,
        timeout=180.0,
    )


def delete_namespace(*, kubeconfig: str) -> OcResult:
    return run_oc(
        [
            "delete",
            "namespace",
            NAMESPACE,
            "--ignore-not-found",
            "--timeout=180s",
        ],
        kubeconfig=kubeconfig,
        timeout=200.0,
    )


def client_pods_running(*, kubeconfig: str) -> bool:
    result = run_oc(
        [
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            "dsx-air/role=client",
            "--field-selector=status.phase=Running",
            "--no-headers",
        ],
        kubeconfig=kubeconfig,
    )
    return result.ok and bool(result.stdout.strip())


def deployments_available(*, kubeconfig: str, role: str) -> bool:
    result = run_oc(
        [
            "get",
            "deploy",
            "-n",
            NAMESPACE,
            "-l",
            f"dsx-air/role={role}",
            "-o",
            "json",
        ],
        kubeconfig=kubeconfig,
    )
    if not result.ok:
        return False
    try:
        items = json.loads(result.stdout).get("items") or []
    except json.JSONDecodeError:
        return False
    if not items:
        return False
    return all(int((item.get("status") or {}).get("availableReplicas") or 0) >= 1 for item in items)


def pod_lines(*, kubeconfig: str) -> str:
    result = run_oc(
        ["get", "pods", "-n", NAMESPACE, "-o", "wide"],
        kubeconfig=kubeconfig,
    )
    text = (result.stdout or result.reason or "").strip()
    return text or "(no pods yet)"


def wait_ready(
    *,
    kubeconfig: str,
    timeout: float = 300.0,
    on_status: Callable[[str], None] | None = None,
) -> OcResult:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        snapshot = pod_lines(kubeconfig=kubeconfig)
        if on_status is not None and snapshot != last:
            on_status(snapshot)
            last = snapshot
        if deployments_available(kubeconfig=kubeconfig, role="server") and deployments_available(
            kubeconfig=kubeconfig, role="client"
        ):
            return OcResult(ok=True, stdout=snapshot, stderr="", reason="")
        time.sleep(5)
    return OcResult(
        ok=False,
        stdout=last,
        stderr="",
        reason=f"pods not Ready within {int(timeout)}s\n{last}",
    )

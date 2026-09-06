#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.workload import (  # noqa: E402
    dedicated_ready_workers,
    render_manifest,
    worker_pairs,
)


def _node(name: str, *, roles: tuple[str, ...], ready: bool) -> dict:
    labels = {f"node-role.kubernetes.io/{role}": "" for role in roles}
    return {
        "metadata": {"name": name, "labels": labels},
        "status": {
            "conditions": [
                {"type": "Ready", "status": "True" if ready else "False"},
            ]
        },
    }


class WorkerSelectionTests(unittest.TestCase):
    def test_skips_control_plane_even_with_worker_label(self) -> None:
        items = [
            _node("ocp-cp-0", roles=("control-plane", "master", "worker"), ready=True),
            _node("ocp-worker-0", roles=("worker",), ready=True),
            _node("ocp-worker-1", roles=("worker",), ready=True),
            _node("ocp-worker-2", roles=("worker",), ready=False),
        ]
        self.assertEqual(
            dedicated_ready_workers(items),
            ["ocp-worker-0", "ocp-worker-1"],
        )

    def test_ring_pairs_scale_with_worker_count(self) -> None:
        self.assertEqual(worker_pairs(["a"]), [])
        self.assertEqual(worker_pairs(["a", "b"]), [("a", "b"), ("b", "a")])
        self.assertEqual(
            worker_pairs(["a", "b", "c"]),
            [("a", "b"), ("b", "c"), ("c", "a")],
        )


class ManifestTests(unittest.TestCase):
    def test_iperf_pins_servers_and_interval(self) -> None:
        text = render_manifest(
            ["ocp-worker-0", "ocp-worker-1"],
            kind="iperf",
            interval=5,
            duration=0,
        )
        self.assertIn("iperf-server-0", text)
        self.assertIn("iperf-client-1", text)
        self.assertIn("kubernetes.io/hostname: ocp-worker-0", text)
        self.assertIn("registry.redhat.io/ubi9/ubi", text)
        self.assertIn("python3", text)
        self.assertIn("TARGET", text)
        self.assertIn("iperf-1", text)
        self.assertNotIn("docker.io", text)

    def test_web_ring_curls_next_worker(self) -> None:
        text = render_manifest(
            ["ocp-worker-0", "ocp-worker-1"],
            kind="web",
            interval=2,
            duration=0,
        )
        self.assertIn("web-server-0", text)
        self.assertIn("http://web-1:8080/", text)
        self.assertIn("http://web-0:8080/", text)
        self.assertIn("INTERVAL", text)
        self.assertIn("http.server", text)
        self.assertNotIn("runAsUser: 0", text)
        self.assertNotIn("docker.io", text)


if __name__ == "__main__":
    unittest.main()

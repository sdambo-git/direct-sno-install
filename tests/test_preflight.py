#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.preflight import (  # noqa: E402
    NodeReport,
    node_report_from_output,
    pending_kubelet_csrs,
    run_preflight,
)
from dsx_air.tunnel import JumpTarget  # noqa: E402

JUMP = JumpTarget(host="worker.example.com", port=25011, username="ubuntu")


class ParseNodeReportTests(unittest.TestCase):
    def test_parses_expired_cert_and_pod_count(self) -> None:
        report = node_report_from_output(
            "ENDDATE=Nov  5 13:26:42 2026 GMT\nEXPIRED=yes\nPODS=5\n"
        )
        self.assertTrue(report.ssh_ok)
        self.assertEqual(report.cert_end, "Nov  5 13:26:42 2026 GMT")
        self.assertTrue(report.cert_expired)
        self.assertEqual(report.pod_count, 5)
        self.assertTrue(report.needs_fix)

    def test_parses_healthy_node(self) -> None:
        report = node_report_from_output(
            "ENDDATE=Dec 16 13:26:42 2026 GMT\nEXPIRED=no\nPODS=47\n"
        )
        self.assertFalse(report.cert_expired)
        self.assertEqual(report.pod_count, 47)
        self.assertFalse(report.needs_fix)

    def test_missing_cert_file_is_unknown_not_expired(self) -> None:
        report = node_report_from_output("ENDDATE=none\nEXPIRED=unknown\nPODS=0\n")
        self.assertIsNone(report.cert_expired)
        self.assertTrue(report.needs_fix)  # zero pods still counts as broken

    def test_non_numeric_pod_count_is_none(self) -> None:
        report = node_report_from_output("ENDDATE=none\nEXPIRED=unknown\nPODS=\n")
        self.assertIsNone(report.pod_count)


class NeedsFixTests(unittest.TestCase):
    def test_ssh_failure_is_not_needs_fix(self) -> None:
        report = NodeReport(ssh_ok=False, ssh_error="connection refused")
        self.assertFalse(report.needs_fix)

    def test_static_pods_only_is_needs_fix(self) -> None:
        report = NodeReport(ssh_ok=True, cert_expired=False, pod_count=5)
        self.assertTrue(report.needs_fix)

    def test_valid_cert_with_many_pods_is_healthy(self) -> None:
        report = NodeReport(ssh_ok=True, cert_expired=False, pod_count=40)
        self.assertFalse(report.needs_fix)


class PendingKubeletCsrTests(unittest.TestCase):
    def test_only_pending_kubelet_signer_csrs_are_listed(self) -> None:
        csr_json = {
            "items": [
                {
                    "metadata": {"name": "csr-pending-kubelet"},
                    "spec": {"signerName": "kubernetes.io/kube-apiserver-client-kubelet"},
                    "status": {},
                },
                {
                    "metadata": {"name": "csr-approved-kubelet"},
                    "spec": {"signerName": "kubernetes.io/kube-apiserver-client-kubelet"},
                    "status": {"conditions": [{"type": "Approved"}]},
                },
                {
                    "metadata": {"name": "csr-pending-other"},
                    "spec": {"signerName": "kubernetes.io/kube-apiserver-serving"},
                    "status": {},
                },
                {
                    "metadata": {"name": "csr-denied-kubelet"},
                    "spec": {"signerName": "kubernetes.io/kube-apiserver-client-kubelet"},
                    "status": {"conditions": [{"type": "Denied"}]},
                },
            ]
        }
        self.assertEqual(pending_kubelet_csrs(csr_json), ["csr-pending-kubelet"])

    def test_empty_items(self) -> None:
        self.assertEqual(pending_kubelet_csrs({}), [])


class RunPreflightTests(unittest.TestCase):
    def test_healthy_node_skips_fix(self) -> None:
        report = NodeReport(ssh_ok=True, cert_expired=False, pod_count=47)
        with patch("dsx_air.preflight.probe_node", return_value=report):
            result = run_preflight(JUMP, "192.168.200.11", cluster_name="ocp")
        self.assertFalse(result.attempted_fix)
        self.assertEqual(result.approved, ())
        self.assertTrue(any("healthy" in line for line in result.messages))

    def test_expired_cert_auto_approves_csrs(self) -> None:
        report = NodeReport(
            ssh_ok=True,
            cert_end="Nov  5 13:26:42 2026 GMT",
            cert_expired=True,
            pod_count=5,
        )
        kubeconfig = Path("/fake/kubeconfig")
        with patch("dsx_air.preflight.probe_node", return_value=report):
            with patch(
                "dsx_air.preflight.admin_kubeconfig", return_value=kubeconfig
            ) as pick:
                with patch(
                    "dsx_air.preflight.approve_kubelet_csrs",
                    return_value=(["csr-1", "csr-2"], None),
                ) as approve:
                    result = run_preflight(JUMP, "192.168.200.11", cluster_name="ocp")
        pick.assert_called_once_with("ocp")
        approve.assert_called_once_with(JUMP, "192.168.200.11", kubeconfig)
        self.assertTrue(result.fixed)
        self.assertEqual(result.approved, ("csr-1", "csr-2"))
        self.assertTrue(any("approved 2 pending kubelet CSR(s)" in line for line in result.messages))

    def test_ssh_failure_reports_and_skips(self) -> None:
        report = NodeReport(ssh_ok=False, ssh_error="No route to host")
        with patch("dsx_air.preflight.probe_node", return_value=report):
            result = run_preflight(JUMP, "192.168.200.11", cluster_name="ocp")
        self.assertFalse(result.attempted_fix)
        self.assertTrue(any("could not SSH" in line for line in result.messages))

    def test_no_kubeconfig_reports_manual_fix(self) -> None:
        report = NodeReport(ssh_ok=True, cert_expired=True, pod_count=5)
        with patch("dsx_air.preflight.probe_node", return_value=report):
            with patch("dsx_air.preflight.admin_kubeconfig", return_value=None):
                result = run_preflight(JUMP, "192.168.200.11", cluster_name="ocp")
        self.assertFalse(result.attempted_fix)
        self.assertFalse(result.fixed)
        self.assertTrue(result.report.needs_fix)
        self.assertTrue(any("auto-fix" in line for line in result.messages))


if __name__ == "__main__":
    unittest.main()

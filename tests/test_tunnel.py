#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.kubeconfig import (  # noqa: E402
    ensure_tunneled_kubeconfig,
    for_local_oc,
    host_is_loopback,
)
from dsx_air.tunnel import api_server_name  # noqa: E402


class ApiServerNameTests(unittest.TestCase):
    def test_api_sni_from_cluster_and_domain(self) -> None:
        old = {k: os.environ.get(k) for k in ("CLUSTER_NAME", "BASE_DNS_DOMAIN")}
        try:
            os.environ["CLUSTER_NAME"] = "ocp"
            os.environ["BASE_DNS_DOMAIN"] = "dsx.air.local"
            self.assertEqual(api_server_name(), "api.ocp.dsx.air.local")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TunneledKubeconfigTests(unittest.TestCase):
    def test_sets_tls_server_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".cache"
            cache.mkdir()
            src = cache / "kubeconfig.ocp"
            src.write_text(
                yaml.dump(
                    {
                        "clusters": [
                            {
                                "name": "ocp",
                                "cluster": {
                                    "server": "https://api.ocp.dsx.air.local:6443",
                                    "certificate-authority-data": "YWJj",
                                },
                            }
                        ]
                    }
                )
            )
            with patch("dsx_air.kubeconfig.repo_root", return_value=Path(tmp)):
                dst = ensure_tunneled_kubeconfig(cluster_name="ocp")
            cfg = yaml.safe_load(dst.read_text())
            cluster = cfg["clusters"][0]["cluster"]
            self.assertEqual(cluster["server"], "https://127.0.0.1:6443")
            self.assertEqual(cluster["tls-server-name"], "api.ocp.dsx.air.local")
            self.assertTrue(cluster["insecure-skip-tls-verify"])
            self.assertNotIn("certificate-authority-data", cluster)

    def test_for_local_oc_keeps_downloaded_kubeconfig_when_api_is_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".cache"
            cache.mkdir()
            src = cache / "kubeconfig.ocp"
            src.write_text("clusters: [{name: ocp, cluster: {server: https://api.ocp.dsx.air.local:6443}}]\n")
            with patch("dsx_air.kubeconfig.repo_root", return_value=Path(tmp)):
                with patch("dsx_air.kubeconfig.host_is_loopback", return_value=True):
                    path = for_local_oc(cluster_name="ocp")
            self.assertEqual(path, src)

    def test_for_local_oc_rewrites_when_api_is_not_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".cache"
            cache.mkdir()
            src = cache / "kubeconfig.ocp"
            src.write_text(
                yaml.dump(
                    {
                        "clusters": [
                            {
                                "name": "ocp",
                                "cluster": {"server": "https://api.ocp.dsx.air.local:6443"},
                            }
                        ]
                    }
                )
            )
            with patch("dsx_air.kubeconfig.repo_root", return_value=Path(tmp)):
                with patch("dsx_air.kubeconfig.host_is_loopback", return_value=False):
                    path = for_local_oc(cluster_name="ocp")
            self.assertEqual(path, cache / "kubeconfig.ocp.tunnel")

    def test_host_is_loopback(self) -> None:
        fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 6443))]
        with patch("dsx_air.kubeconfig.socket.getaddrinfo", return_value=fake):
            self.assertTrue(host_is_loopback("api.ocp.dsx.air.local"))
        fake_pub = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.200.10", 6443))]
        with patch("dsx_air.kubeconfig.socket.getaddrinfo", return_value=fake_pub):
            self.assertFalse(host_is_loopback("api.ocp.dsx.air.local"))


if __name__ == "__main__":
    unittest.main()

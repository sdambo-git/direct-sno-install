#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from air_common import cluster_dns_host_block, merge_hosts_file  # noqa: E402
from dsx_air.commands.console import _chrome_args  # noqa: E402


class ChromeArgsTests(unittest.TestCase):
    def test_maps_apps_and_api_and_disables_async_dns(self) -> None:
        args = _chrome_args(
            binary=Path("/usr/bin/chromium"),
            cluster_name="ocp",
            domain="dsx.air.local",
            ingress_vip="192.168.200.11",
            api_vip="192.168.200.10",
            url="https://console-openshift-console.apps.ocp.dsx.air.local",
        )
        joined = " ".join(args)
        self.assertIn("MAP *.apps.ocp.dsx.air.local 192.168.200.11", joined)
        self.assertIn("MAP api.ocp.dsx.air.local 192.168.200.10", joined)
        self.assertIn("--disable-features=AsyncDns", args)
        self.assertIn("--dns-over-https-mode=off", args)
        self.assertIn("--ozone-platform=x11", args)
        self.assertIn("--proxy-server=socks5://127.0.0.1:1080", args)


class JumpHostDnsTests(unittest.TestCase):
    def test_hosts_block_maps_api_and_console(self) -> None:
        block = cluster_dns_host_block(
            cluster_name="ocp",
            domain="dsx.air.local",
            api_vip="192.168.200.10",
            ingress_vip="192.168.200.11",
        )
        self.assertIn("192.168.200.10 api.ocp.dsx.air.local api-int.ocp.dsx.air.local", block)
        self.assertIn("console-openshift-console.apps.ocp.dsx.air.local", block)
        self.assertIn("oauth-openshift.apps.ocp.dsx.air.local", block)
        self.assertIn("192.168.200.11", block)

    def test_merge_replaces_existing_block(self) -> None:
        old = (
            "127.0.0.1 localhost\n"
            "# BEGIN dsx-air-ocp\n"
            "1.2.3.4 stale.example\n"
            "# END dsx-air-ocp\n"
            "10.0.0.1 keep-me\n"
        )
        new_block = cluster_dns_host_block(
            cluster_name="ocp",
            domain="dsx.air.local",
            api_vip="192.168.200.10",
            ingress_vip="192.168.200.11",
        )
        merged = merge_hosts_file(old, new_block)
        self.assertIn("127.0.0.1 localhost", merged)
        self.assertIn("10.0.0.1 keep-me", merged)
        self.assertIn("192.168.200.11", merged)
        self.assertNotIn("stale.example", merged)
        self.assertEqual(merged.count("# BEGIN dsx-air-ocp"), 1)


if __name__ == "__main__":
    unittest.main()

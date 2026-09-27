#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.ibi import (  # noqa: E402
    DEFAULT_SEED_IMAGE,
    write_installation_config,
    write_site_configs,
)
from dsx_air.spec import load_spec  # noqa: E402
import yaml  # noqa: E402


class IbiTests(unittest.TestCase):
    def test_ibi_target_spec(self) -> None:
        spec = load_spec(ROOT / "examples" / "ibi-target.yaml")
        self.assertEqual(spec.simulation.name, "dsx-ibi-target")
        self.assertEqual(spec.expected_hosts, 1)
        self.assertEqual(spec.profile, "sno")
        self.assertIsNone(spec.auth.ai_offlinetoken_file)

    def test_write_installation_config_uses_g_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            pull = dest / "pull.json"
            pub = dest / "id.pub"
            pull.write_text(json.dumps({"auths": {"quay.io": {"auth": "e30="}}}))
            pub.write_text("ssh-ed25519 AAAAtest comment\n")
            with patch("dsx_air.ibi.env_config.pull_secret_path", return_value=pull):
                with patch("dsx_air.ibi.env_config.ssh_public_key", return_value=pub.read_text().strip()):
                    path = write_installation_config(dest, seed_image=DEFAULT_SEED_IMAGE)
            data = yaml.safe_load(path.read_text())
            self.assertEqual(data["extraPartitionStart"], "100G")
            self.assertEqual(data["seedImage"], DEFAULT_SEED_IMAGE)
            self.assertNotIn("GiB", data["extraPartitionStart"])
            self.assertIn("quay.io", data["pullSecret"])

    def test_write_site_configs(self) -> None:
        spec = load_spec(ROOT / "examples" / "ibi-target.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            pull = dest / "pull.json"
            pull.write_text(json.dumps({"auths": {"quay.io": {"auth": "e30="}}}))
            with patch("dsx_air.ibi.env_config.pull_secret_path", return_value=pull):
                with patch("dsx_air.ibi.env_config.ssh_public_key", return_value="ssh-ed25519 AAAA"):
                    with patch("dsx_air.ibi.env_config.base_dns_domain", return_value="dsx.air.local"):
                        write_site_configs(dest, spec, hostname="ocp-cp-0")
            install = yaml.safe_load((dest / "install-config.yaml").read_text())
            ibc = yaml.safe_load((dest / "image-based-config.yaml").read_text())
            self.assertEqual(install["metadata"]["name"], "ocp")
            self.assertEqual(install["platform"], {"none": {}})
            self.assertEqual(ibc["hostname"], "ocp-cp-0")
            self.assertEqual(ibc["kind"], "ImageBasedConfig")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dsx_air.spec import LabSpec, load_spec  # noqa: E402
from dsx_air.topology import node_names, render_manifest  # noqa: E402


class SpecTests(unittest.TestCase):
    def test_example_yaml_loads(self) -> None:
        spec = load_spec(ROOT / "examples" / "ha-3cp-2w.yaml")
        self.assertEqual(spec.simulation.name, "dsx-lab")
        self.assertEqual(spec.cluster.name, "ocp")
        self.assertEqual(spec.cluster.control_plane.count, 3)
        self.assertEqual(spec.cluster.workers.count, 2)
        self.assertEqual(spec.expected_hosts, 5)
        self.assertEqual(spec.profile, "multinode")

    def test_merge_overrides(self) -> None:
        spec = load_spec(ROOT / "examples" / "ha-3cp-2w.yaml")
        merged = spec.merge(sim="other-lab", workers=1, ocp_version="4.20")
        self.assertEqual(merged.simulation.name, "other-lab")
        self.assertEqual(merged.cluster.workers.count, 1)
        self.assertEqual(merged.cluster.version, "4.20")
        self.assertEqual(merged.cluster.control_plane.count, 3)

    def test_topology_names_and_roles(self) -> None:
        spec = LabSpec.model_validate(
            {
                "simulation": {"name": "dsx-lab"},
                "cluster": {
                    "name": "ocp",
                    "version": "4.19",
                    "control_plane": {"count": 3},
                    "workers": {"count": 2, "cpu": 8, "memory_mb": 32768},
                },
            }
        )
        names = node_names(spec)
        self.assertEqual(
            names,
            ["ocp-cp-0", "ocp-cp-1", "ocp-cp-2", "ocp-worker-0", "ocp-worker-1"],
        )
        manifest = render_manifest(spec, cdrom="dsxair-discovery-test")
        self.assertEqual(manifest["name"], "dsx-lab")
        self.assertEqual(manifest["content"]["links"], [])
        self.assertEqual(set(manifest["content"]["nodes"]), set(names))
        worker = manifest["content"]["nodes"]["ocp-worker-0"]
        self.assertEqual(worker["cpu"], 8)
        self.assertEqual(worker["memory"], 32768)
        self.assertEqual(worker["boot"], ["hd", "cdrom"])
        json.dumps(manifest)


class EnvironFromSpecTests(unittest.TestCase):
    def test_apply_to_environ_sets_sim_and_cluster(self) -> None:
        import os

        from dsx_air.spec import apply_to_environ
        import env_config

        spec = load_spec(ROOT / "examples" / "ha-3cp-2w.yaml")
        old = {k: os.environ.get(k) for k in ("CLUSTER_NAME", "SIMULATION_NAME", "CLUSTER_PROFILE")}
        try:
            apply_to_environ(spec)
            self.assertEqual(os.environ["CLUSTER_NAME"], "ocp")
            self.assertEqual(os.environ["SIMULATION_NAME"], "dsx-lab")
            self.assertEqual(env_config.cluster_name(), "ocp")
            self.assertEqual(env_config.simulation_name(), "dsx-lab")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class AuthResolutionTests(unittest.TestCase):
    def test_air_api_key_reads_default_file(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import env_config

        old = os.environ.pop("AIR_API_KEY", None)
        old_file = os.environ.pop("AIR_API_KEY_FILE", None)
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "air-api-key"
            key_path.write_text("from-default-file\n")
            try:
                with patch.object(env_config, "DEFAULT_AIR_API_KEY_FILE", key_path):
                    self.assertEqual(env_config.air_api_key(), "from-default-file")
                    self.assertTrue(env_config.air_api_key_configured())
            finally:
                if old is None:
                    os.environ.pop("AIR_API_KEY", None)
                else:
                    os.environ["AIR_API_KEY"] = old
                if old_file is None:
                    os.environ.pop("AIR_API_KEY_FILE", None)
                else:
                    os.environ["AIR_API_KEY_FILE"] = old_file


if __name__ == "__main__":
    unittest.main()

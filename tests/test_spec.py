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
import yaml  # noqa: E402


class SpecTests(unittest.TestCase):
    def test_example_yaml_loads(self) -> None:
        spec = load_spec(ROOT / "examples" / "ha-3cp-2w.yaml")
        self.assertEqual(spec.simulation.name, "dsx-ocp-shahar")
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
        self.assertEqual(worker["os"], "blank-100g")
        json.dumps(manifest)

    def test_sno_large_disk_uses_blank_300g(self) -> None:
        spec = LabSpec.model_validate(
            {
                "simulation": {"name": "dsx-sno-ibi"},
                "cluster": {
                    "name": "ocp",
                    "version": "4.22",
                    "control_plane": {"count": 1, "disk_gb": 300},
                    "workers": {"count": 0},
                },
            }
        )
        self.assertEqual(spec.profile, "sno")
        manifest = render_manifest(spec, cdrom="dsxair-discovery-test")
        node = manifest["content"]["nodes"]["ocp-cp-0"]
        self.assertEqual(node["storage"], 300)
        self.assertEqual(node["os"], "blank-300g")

    def test_sno_yaml_enables_200g_containers_partition(self) -> None:
        spec = load_spec(ROOT / "examples" / "sno.yaml")
        self.assertEqual(spec.profile, "sno")
        self.assertEqual(spec.cluster.control_plane.disk_gb, 300)
        self.assertEqual(spec.cluster.containers_partition.gb, 200)
        self.assertEqual(spec.cluster.containers_partition.start_gb, 100)
        self.assertEqual(spec.cluster.containers_partition.device, "/dev/vda")

    def test_containers_partition_must_fit_disk(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            LabSpec.model_validate(
                {
                    "simulation": {"name": "tiny"},
                    "cluster": {
                        "name": "ocp",
                        "version": "4.22",
                        "control_plane": {"count": 1, "disk_gb": 200},
                        "workers": {"count": 0},
                        "containers_partition": {"gb": 200, "start_gb": 100},
                    },
                }
            )


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
            self.assertEqual(os.environ["SIMULATION_NAME"], "dsx-ocp-shahar")
            self.assertEqual(env_config.cluster_name(), "ocp")
            self.assertEqual(env_config.simulation_name(), "dsx-ocp-shahar")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_apply_to_environ_sets_containers_partition(self) -> None:
        import os

        from dsx_air.spec import apply_to_environ
        import env_config

        spec = load_spec(ROOT / "examples" / "sno.yaml")
        keys = (
            "CLUSTER_NAME",
            "CONTAINERS_PARTITION_GB",
            "CONTAINERS_PARTITION_START_GB",
            "CONTAINERS_PARTITION_DEVICE",
        )
        old = {k: os.environ.get(k) for k in keys}
        try:
            apply_to_environ(spec)
            self.assertEqual(os.environ["CONTAINERS_PARTITION_GB"], "200")
            self.assertEqual(env_config.containers_partition_gb(), 200)
            self.assertEqual(env_config.containers_partition_start_gb(), 100)
            self.assertEqual(env_config.containers_partition_device(), "/dev/vda")
            from containers_partition import MANIFEST_FILE, assisted_openshift_manifests

            items = assisted_openshift_manifests()
            self.assertEqual(len(MANIFEST_FILE), len("98-containers-part.yaml"))
            self.assertLessEqual(len(MANIFEST_FILE), 30)
            self.assertEqual(list(items[0].keys()), [MANIFEST_FILE])
            body = yaml.safe_load(items[0][MANIFEST_FILE])
            part = body["spec"]["config"]["storage"]["disks"][0]["partitions"][0]
            self.assertEqual(part["startMiB"], 100 * 1024)
            self.assertEqual(part["sizeMiB"], 200 * 1024 - 1024)
            self.assertEqual(part["label"], "var-lib-containers")
            self.assertEqual(
                body["spec"]["config"]["storage"]["disks"][0]["device"], "/dev/vda"
            )
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


class LastSpecTests(unittest.TestCase):
    def test_activate_spec_none_uses_last_spec(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        from dsx_air.spec import activate_spec, remember_spec
        import env_config

        keys = ("CLUSTER_NAME", "SIMULATION_NAME", "CLUSTER_PROFILE", "TOPOLOGY_PATH")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            spec_file = cache / "lab.yaml"
            spec_file.write_text(
                "simulation:\n  name: remembered-sim\n"
                "cluster:\n  name: ocp\n  version: '4.22'\n"
                "  control_plane: { count: 3 }\n"
                "  workers: { count: 2 }\n"
            )
            try:
                with patch("dsx_air.pipeline.cache_dir", return_value=cache):
                    remember_spec(spec_file)
                    spec = activate_spec(None)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(spec.simulation.name, "remembered-sim")
                self.assertEqual(env_config.simulation_name(), "remembered-sim")
                self.assertEqual(env_config.cluster_name(), "ocp")
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_activate_spec_none_uses_single_cached_topology(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch

        from dsx_air.spec import activate_spec
        import env_config

        keys = ("CLUSTER_NAME", "SIMULATION_NAME", "TOPOLOGY_PATH")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            topo = cache / "dsx-ocp-shahar" / "topology.json"
            topo.parent.mkdir()
            topo.write_text(json.dumps({"name": "dsx-ocp-shahar"}))
            os.environ["SIMULATION_NAME"] = "ocp-cluster"
            try:
                with patch("dsx_air.pipeline.cache_dir", return_value=cache):
                    self.assertIsNone(activate_spec(None))
                self.assertEqual(env_config.simulation_name(), "dsx-ocp-shahar")
                self.assertEqual(os.environ["TOPOLOGY_PATH"], str(topo))
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()

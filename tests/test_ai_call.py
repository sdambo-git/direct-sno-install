#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from assisted_common import (  # noqa: E402
    ai_call,
    assign_topology_roles,
    is_unauthorized,
    topology_name_for_host,
)
from assisted_poll import PollIssue, PollTracker  # noqa: E402
import env_config  # noqa: E402
import os  # noqa: E402


class UnauthorizedTests(unittest.TestCase):
    def test_detects_401_text(self) -> None:
        self.assertTrue(is_unauthorized(Exception("(401) token is invalid")))
        self.assertFalse(is_unauthorized(Exception("timed out")))

    def test_ai_call_retries_once(self) -> None:
        class Fake:
            token = "old"
            offlinetoken = "offline"
            quiet = True

            def refresh_token(self, token, offlinetoken):
                self.token = "new"

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception('{"code":401,"message":"Error parsing token or token is invalid"}')
            return "ok"

        ai = Fake()
        self.assertEqual(ai_call(ai, fn), "ok")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(ai.token, "new")


class NtpTrackerTests(unittest.TestCase):
    def test_ntp_insufficient_does_not_abort(self) -> None:
        tracker = PollTracker()
        issue = PollIssue(
            severity="warn",
            code="host-insufficient",
            message="Host 'ocp-cp-1' insufficient: couldn't synchronize with any NTP server",
        )
        for _ in range(20):
            tracker.record_issues([issue])
            abort, _ = tracker.should_abort()
            self.assertFalse(abort)


class TopologyRoleTests(unittest.TestCase):
    NAMES = ["ocp-cp-0", "ocp-cp-1", "ocp-cp-2", "ocp-worker-0", "ocp-worker-1"]

    def test_matches_requested_hostname(self) -> None:
        host = {"requested_hostname": "ocp-worker-0", "role": "master"}
        self.assertEqual(topology_name_for_host(host, self.NAMES), "ocp-worker-0")

    def test_uuid_is_not_mapped_by_arrival_order(self) -> None:
        host = {"id": "3092a717-6692-426e-a109-6162be135a31", "requested_hostname": ""}
        self.assertIsNone(topology_name_for_host(host, self.NAMES))

    def test_ocp_cp_1_does_not_match_ocp_cp_10(self) -> None:
        names = [*self.NAMES, "ocp-cp-10"]
        host = {"requested_hostname": "ocp-cp-1"}
        self.assertEqual(topology_name_for_host(host, names), "ocp-cp-1")

    def test_inventory_hostname(self) -> None:
        import json

        host = {"inventory": json.dumps({"hostname": "ocp-cp-2.example.com"})}
        self.assertEqual(topology_name_for_host(host, self.NAMES), "ocp-cp-2")

    def test_pins_worker_by_uuid_not_hostname(self) -> None:
        from unittest.mock import patch

        class Fake:
            def __init__(self) -> None:
                self.updates: list[tuple[str, dict]] = []

            def update_host(self, ident, payload):
                self.updates.append((ident, payload))

        worker_id = "6b7cc8ca-60a8-4fb1-a6f8-e0053ed95f7f"
        hosts = [
            {
                "id": worker_id,
                "requested_hostname": "ocp-worker-0",
                "role": "auto-assign",
                "status": "insufficient",
            },
            {
                "id": "cp0-id",
                "requested_hostname": "ocp-cp-0",
                "role": "master",
                "status": "known",
            },
            {"requested_hostname": "ocp-worker-1", "role": "auto-assign", "status": "known"},
            {
                "id": "installed-other-cluster",
                "requested_hostname": "ocp-cp-1",
                "role": "auto-assign",
                "status": "installed",
            },
        ]
        ai = Fake()
        with (
            patch("assisted_common.env_config.is_multinode", return_value=True),
            patch("assisted_common.env_config.topology_node_names", return_value=self.NAMES),
            patch(
                "assisted_common.env_config.host_role_for_topology_node",
                side_effect=lambda n: "worker" if "worker" in n else "master",
            ),
        ):
            changed = assign_topology_roles(ai, hosts)
        self.assertEqual(changed, [("ocp-worker-0", worker_id, "worker")])
        self.assertEqual(ai.updates, [(worker_id, {"role": "worker"})])

    def test_installed_state_500_does_not_abort(self) -> None:
        from unittest.mock import patch

        class Fake:
            def update_host(self, ident, payload):
                raise Exception(
                    "(500) Host is in installed state, host role can be set only "
                    "in one of [discovering known disconnected insufficient "
                    "pending-for-input] states"
                )

        hosts = [
            {
                "id": "e45596df-54e4-4ef8-ae69-2d32fa649602",
                "requested_hostname": "ocp-cp-1",
                "role": "auto-assign",
                "status": "known",
            }
        ]
        with (
            patch("assisted_common.env_config.is_multinode", return_value=True),
            patch("assisted_common.env_config.topology_node_names", return_value=self.NAMES),
            patch(
                "assisted_common.env_config.host_role_for_topology_node",
                return_value="master",
            ),
        ):
            changed = assign_topology_roles(Fake(), hosts)
        self.assertEqual(changed, [])


class DiscoveryTimeoutTests(unittest.TestCase):
    def test_scales_with_host_count(self) -> None:
        old = os.environ.pop("DISCOVERY_TIMEOUT", None)
        try:
            self.assertEqual(env_config.discovery_timeout_seconds(1), 20 * 60)
            self.assertEqual(env_config.discovery_timeout_seconds(5), 40 * 60)
            os.environ["DISCOVERY_TIMEOUT"] = "60"
            self.assertEqual(env_config.discovery_timeout_seconds(5), 60 * 60)
        finally:
            if old is None:
                os.environ.pop("DISCOVERY_TIMEOUT", None)
            else:
                os.environ["DISCOVERY_TIMEOUT"] = old


if __name__ == "__main__":
    unittest.main()

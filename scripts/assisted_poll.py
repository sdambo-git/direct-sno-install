#!/usr/bin/env python3
"""Polling helpers: detect Assisted Installer / Air states that need attention."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field

import env_config

# Host or cluster statuses that usually need a human or recovery script.
ACTION_HOST_STATUSES = frozenset(
    {
        "error",
        "cancelled",
        "installing-pending-user-action",
        "resetting-pending-user-action",
        "unbinding-pending-user-action",
    }
)

ACTION_CLUSTER_STATUSES = frozenset(
    {
        "error",
        "cancelled",
        "installing-pending-user-action",
    }
)

# Shorter sleep while install is in a fragile phase.
FAST_POLL_STATUSES = frozenset(
    {
        "installing",
        "installing-in-progress",
        "installing-pending-user-action",
    }
)

FAST_POLL_STAGES = frozenset(
    {
        "Writing image to disk",
        "Rebooting",
        "Waiting for ignition",
        "Configuring",
    }
)

REMEDIATION_HINTS: dict[str, str] = {
    "installing-pending-user-action": (
        "Host must boot the installed disk and reach the cluster API on the OOB IP. "
        "Check the Air node console. If the node is offline after boot-to-disk, verify "
        "cpu_mode=host-passthrough and run: uv run scripts/bootstrap_jump_host.py && "
        "probe from jump host: ping <oob-ip>. DNS for api.<cluster>.<domain> must "
        "resolve on oob-mgmt-server."
    ),
    "resetting-pending-user-action": (
        "Host must boot the discovery ISO to finish AI reset. Ensure cdrom is attached, "
        "boot order is ['cdrom', 'hd'], and the Air image matches the current infraenv "
        "(upload a new image name after 00 --force). Consider node.rebuild()."
    ),
    "no_oob_after_reboot": (
        "OOB IP is not reachable from the jump host after boot-to-disk. Check Air "
        "console for boot errors. Ensure boot_node_to_disk preserved cpu_mode "
        "host-passthrough."
    ),
    "wrong_discovery_boot": (
        f"Air node boot order is disk-first or cdrom is missing — discovery ISO will "
        f"not boot. topology.json should use boot ['cdrom', 'hd'] and a current "
        f"discovery image."
    ),
    "insufficient": (
        "Host validations are failing — read status_info (common: NTP). Wait briefly; "
        "if it persists, check Assisted Installer host details."
    ),
    "install-connect-timeout": (
        "HA bootstrap could not SSH to a master after image write. The bootstrap "
        "node stays at 'Starting installation' / bootstrap while the others should "
        "leave 'Writing image to disk 100%' and Reboot onto the installed disk. "
        "Check those nodes' Air consoles. Recover all masters in one run: "
        "uv run 09_recover_to_discovery.py --all --reset-ai, then re-run from "
        "host discovery."
    ),
}


@dataclass
class PollIssue:
    severity: str  # "action" | "warn"
    code: str
    message: str
    hint: str = ""


@dataclass
class PollSnapshot:
    cluster_status: str = ""
    cluster_status_info: str = ""
    hosts: list[dict] = field(default_factory=list)
    issues: list[PollIssue] = field(default_factory=list)

    def has_action_required(self) -> bool:
        return any(i.severity == "action" for i in self.issues)


def host_stage(host: dict) -> tuple[str, str]:
    progress = host.get("progress") or {}
    return str(progress.get("current_stage") or ""), str(progress.get("progress_info") or "")


def refresh_ai_token(ai) -> None:
    """Refresh the Assisted Installer SaaS token (ailib requires both args)."""
    ai.refresh_token(ai.token, ai.offlinetoken)


def get_cluster_dict(ai, cluster_name: str | None = None) -> dict:
    """Fetch cluster with token refresh on 401."""
    name = cluster_name or env_config.cluster_name()
    cluster_id = ai.get_cluster_id(name)
    try:
        return ai.client.v2_get_cluster(cluster_id=cluster_id).to_dict()
    except Exception as exc:  # noqa: BLE001
        if "401" in str(exc) and hasattr(ai, "refresh_token"):
            refresh_ai_token(ai)
            return ai.client.v2_get_cluster(cluster_id=cluster_id).to_dict()
        raise


def analyze_hosts(cluster: dict, hosts: list[dict]) -> list[PollIssue]:
    issues: list[PollIssue] = []
    c_status = cluster.get("status") or ""
    if c_status in ACTION_CLUSTER_STATUSES:
        issues.append(
            PollIssue(
                severity="action",
                code=f"cluster-{c_status}",
                message=f"Cluster status {c_status!r}: {cluster.get('status_info') or ''}",
                hint=REMEDIATION_HINTS.get(c_status, ""),
            )
        )

    for host in hosts:
        h_status = host.get("status") or ""
        hostname = host.get("requested_hostname") or host.get("id")
        info = host.get("status_info") or ""
        if h_status in ACTION_HOST_STATUSES:
            hint = REMEDIATION_HINTS.get(h_status, "")
            if "timeout while connecting to host" in info.lower():
                hint = REMEDIATION_HINTS["install-connect-timeout"]
            issues.append(
                PollIssue(
                    severity="action",
                    code=f"host-{h_status}",
                    message=f"Host {hostname!r} status {h_status!r}: {info}",
                    hint=hint,
                )
            )
        elif h_status == "insufficient" and info:
            issues.append(
                PollIssue(
                    severity="warn",
                    code="host-insufficient",
                    message=f"Host {hostname!r} insufficient: {info}",
                    hint=REMEDIATION_HINTS["insufficient"],
                )
            )
    return issues


def probe_oob_ping(oob_ip: str) -> bool:
    """Ping OOB IP from jump host. Returns False if probe cannot run or ping fails."""
    if not os.environ.get("AIR_API_KEY") and not os.environ.get("AIR_API_KEY_FILE"):
        return True  # skip when Air creds absent
    try:
        from air_common import ensure_jump_host_ready, get_api, get_simulation

        sim = get_simulation(get_api())
        service, server = ensure_jump_host_ready(sim)
        port = service.worker_port
        worker = service.worker_fqdn
        user = getattr(server.image, "default_username", "ubuntu")
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-p",
                str(port),
                f"{user}@{worker}",
                f"ping -c1 -W3 {oob_ip} >/dev/null 2>&1",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return True  # do not false-alarm if jump host is not ready yet


def _node_discovery_boot_issues(name: str, node) -> list[PollIssue]:
    boot = (node.advanced or {}).get("boot")
    cdrom = node.cdrom
    boot_list = boot if isinstance(boot, list) else [boot] if boot else []
    recover_cmd = f"uv run scripts/09_recover_to_discovery.py --node {name}"
    if boot in ("hd", ["hd"]) and not cdrom:
        return [
            PollIssue(
                severity="action",
                code=f"no-bootable-device-risk-{name}",
                message=f"{name}: boot={boot!r} with cdrom detached — "
                "will show 'No bootable device' if disk is blank",
                hint=f"Run {recover_cmd} to rebuild blank disk and re-attach "
                "discovery ISO with boot ['hd', 'cdrom'].",
            )
        ]
    if boot_list and boot_list[0] == "cdrom":
        return [
            PollIssue(
                severity="warn",
                code=f"wrong-discovery-boot-{name}",
                message=f"{name}: boot={boot!r} cdrom={cdrom!r}",
                hint=REMEDIATION_HINTS["wrong_discovery_boot"],
            )
        ]
    if not cdrom:
        return [
            PollIssue(
                severity="action",
                code=f"cdrom-missing-{name}",
                message=f"{name}: discovery ISO not attached (boot={boot!r})",
                hint=f"Run {recover_cmd}",
            )
        ]
    return []


def check_air_discovery_boot() -> list[PollIssue]:
    """Warn when any topology node isn't configured to boot the discovery ISO.

    Checks every node from topology.json (all 3 for multinode), not just one
    — a single stale/misattached node would otherwise stay silent all the way
    to the poll timeout.
    """
    if not os.environ.get("AIR_API_KEY") and not os.environ.get("AIR_API_KEY_FILE"):
        return []
    try:
        from air_common import get_api, get_simulation

        sim = get_simulation(get_api())
        node_names = env_config.topology_node_names() or [env_config.cluster_name()]
        nodes_by_name = {n.name: n for n in sim.nodes.list()}
        issues: list[PollIssue] = []
        for name in node_names:
            node = nodes_by_name.get(name)
            if node is None:
                issues.append(
                    PollIssue(
                        severity="action",
                        code=f"node-missing-{name}",
                        message=f"{name}: not found in simulation {sim.name!r}",
                        hint="Run uv run scripts/01_create_simulation.py.",
                    )
                )
                continue
            issues.extend(_node_discovery_boot_issues(name, node))
        return issues
    except Exception:  # noqa: BLE001
        return []


def check_air_post_boot_to_disk() -> list[PollIssue]:
    """Warn when post-install boot settings look wrong."""
    if not os.environ.get("AIR_API_KEY") and not os.environ.get("AIR_API_KEY_FILE"):
        return []
    try:
        from air_common import get_api, get_node, get_simulation

        node = get_node(get_simulation(get_api()))
        boot = (node.advanced or {}).get("boot")
        cpu_mode = (node.advanced or {}).get("cpu_mode")
        issues: list[PollIssue] = []
        if node.cdrom is not None:
            issues.append(
                PollIssue(
                    severity="warn",
                    code="cdrom-still-attached",
                    message=f"Discovery cdrom still attached after boot-to-disk: {node.cdrom!r}",
                    hint="Run uv run scripts/03_boot_to_disk.py or fix boot_node_to_disk.",
                )
            )
        if boot not in ("hd", ["hd"]):
            issues.append(
                PollIssue(
                    severity="warn",
                    code="boot-not-hd",
                    message=f"Expected hd-only boot after install handoff, got {boot!r}",
                    hint="Run uv run scripts/03_boot_to_disk.py.",
                )
            )
        if cpu_mode and cpu_mode != "host-passthrough":
            issues.append(
                PollIssue(
                    severity="action",
                    code="cpu-mode-regression",
                    message=f"cpu_mode is {cpu_mode!r} (expected host-passthrough)",
                    hint="Patch node advanced.cpu_mode and reboot — custom mode can break disk boot.",
                )
            )
        return issues
    except Exception:  # noqa: BLE001
        return []


def suggest_poll_interval(snapshot: PollSnapshot) -> int:
    """Return recommended seconds until next poll."""
    for host in snapshot.hosts:
        if (host.get("status") or "") in FAST_POLL_STATUSES:
            return 15
        stage, _ = host_stage(host)
        if stage in FAST_POLL_STAGES:
            return 15
    if snapshot.cluster_status in ACTION_CLUSTER_STATUSES:
        return 15
    return 30


def format_issues(issues: list[PollIssue]) -> str:
    if not issues:
        return ""
    lines = ["", "=" * 72, "ATTENTION — review recommended", "=" * 72]
    for issue in issues:
        prefix = "ACTION" if issue.severity == "action" else "WARN"
        lines.append(f"[{prefix}] {issue.message}")
        if issue.hint:
            lines.append(f"         → {issue.hint}")
    lines.append("=" * 72)
    return "\n".join(lines)


def print_action_block(issues: list[PollIssue], *, consecutive: int = 1) -> None:
    block = format_issues(issues)
    if not block:
        return
    if consecutive > 1:
        block += f"\n(repeated {consecutive} consecutive polls)\n"
    print(block, flush=True)


@dataclass
class PollTracker:
    """Track repeated issues so we fail fast instead of polling silently for hours."""

    action_streak: dict[str, int] = field(default_factory=dict)
    oob_down_streak: int = 0
    boot_to_disk_at: float | None = None

    def record_issues(self, issues: list[PollIssue]) -> int:
        action_codes = {i.code for i in issues if i.severity == "action"}
        for code in list(self.action_streak):
            if code not in action_codes:
                self.action_streak[code] = 0
        max_streak = 0
        for code in action_codes:
            self.action_streak[code] = self.action_streak.get(code, 0) + 1
            max_streak = max(max_streak, self.action_streak[code])
        return max_streak

    def note_boot_to_disk(self) -> None:
        self.boot_to_disk_at = time.monotonic()
        self.oob_down_streak = 0

    def check_oob_after_boot(self, oob_ip: str, *, grace_s: int = 120) -> list[PollIssue]:
        if self.boot_to_disk_at is None:
            return []
        if time.monotonic() - self.boot_to_disk_at < grace_s:
            return []
        if probe_oob_ping(oob_ip):
            self.oob_down_streak = 0
            return []
        self.oob_down_streak += 1
        if self.oob_down_streak < 2:
            return [
                PollIssue(
                    severity="warn",
                    code="oob-unreachable",
                    message=f"OOB {oob_ip} not pingable from jump host (poll {self.oob_down_streak})",
                    hint=REMEDIATION_HINTS["no_oob_after_reboot"],
                )
            ]
        return [
            PollIssue(
                severity="action",
                code="no_oob_after_reboot",
                message=(
                    f"OOB {oob_ip} still unreachable {self.oob_down_streak} polls after "
                    f"boot-to-disk"
                ),
                hint=REMEDIATION_HINTS["no_oob_after_reboot"],
            )
        ]

    def should_abort(self, max_action_streak: int = 3) -> tuple[bool, str]:
        for code, streak in self.action_streak.items():
            if streak >= max_action_streak:
                return True, f"Repeated action-required state {code!r} ({streak} polls)"
        if self.oob_down_streak >= 4:
            return True, "OOB IP unreachable too long after boot-to-disk"
        return False, ""

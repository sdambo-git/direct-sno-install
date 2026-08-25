#!/usr/bin/env python3
"""
Shared helpers used by the numbered scripts in this directory
(01_create_simulation.py, 02_attach_discovery_iso.py,
03_boot_to_disk.py). Not meant to be run directly.

Handles two annoying, empirically-discovered Air quirks:

- Changing a node's `cdrom`/`advanced.boot` fields requires the simulation
  to be fully INACTIVE first (patching while ACTIVE is silently accepted
  for some fields but rejected or ignored for others).
- Air auto-creates a checkpoint on shutdown, and that checkpoint must
  reach the COMPLETE state before it (or the simulation) can be
  manipulated further, otherwise you get:
      "The checkpoint must be in the `COMPLETE` state."
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from air_sdk import AirApi
from air_sdk.endpoints.nodes import Node
from air_sdk.endpoints.services import Service
from air_sdk.endpoints.simulations import Simulation

import env_config
from upload_discovery_iso import get_api  # noqa: F401  (re-exported)

# Backward-compatible defaults for scripts that import these constants.
SIMULATION_NAME = env_config.DEFAULT_CLUSTER_NAME
NODE_NAME = env_config.DEFAULT_CLUSTER_NAME

# Air auto-provisions these two nodes whenever a node's eth0 is left on the
# default OOB network — they're never defined in topology.json.
OOB_SERVER_NAME = "oob-mgmt-server"
OOB_SERVER_INTERFACE = "eth0"
JUMP_HOST_SERVICE_NAME = "oob-mgmt-server SSH"


def get_simulation(api: AirApi, name: str | None = None) -> Simulation:
    sim_name = name or env_config.simulation_name()
    sims = list(api.simulations.list(search=sim_name))
    matches = [s for s in sims if s.name == sim_name]
    if not matches:
        raise SystemExit(
            f"No simulation named {sim_name!r} found. Run 01_create_simulation.py first."
        )
    return matches[0]


def get_topology_nodes(sim: Simulation) -> list[Node]:
    """Return OCP nodes from the simulation (exclude Air-managed OOB infra)."""
    implicit = {OOB_SERVER_NAME, "oob-mgmt-switch-leaf-1"}
    return [node for node in sim.nodes.list() if node.name not in implicit]


def default_node_name() -> str:
    names = env_config.topology_node_names()
    return names[0] if names else env_config.cluster_name()


def get_node(sim: Simulation, name: str | None = None) -> Node:
    node_name = name or default_node_name()
    for node in sim.nodes.list():
        if node.name == node_name:
            return node
    raise SystemExit(f"No node named {node_name!r} found in simulation {sim.name!r}.")


# A 3-node HA sim (plus oob-mgmt-server / switch) can sit in SHUTTING_DOWN
# well past 4 minutes. Calling start() from that state is rejected by Air.
SHUTDOWN_TIMEOUT = 600
START_TIMEOUT = 300


def wait_for_sim_state(sim: Simulation, *states: str, timeout: int = 180, interval: int = 4) -> None:
    started = time.monotonic()
    deadline = started + timeout
    while True:
        sim.refresh()
        elapsed = int(time.monotonic() - started)
        print(f"  simulation state: {sim.state} ({elapsed}s)")
        if sim.state in states:
            return
        if time.monotonic() > deadline:
            raise SystemExit(
                f"Timed out after {timeout}s waiting for simulation state in {states} "
                f"(last seen: {sim.state!r}). If it is still SHUTTING_DOWN, wait "
                "until the Air UI shows stopped, then re-run — do not start() from "
                "SHUTTING_DOWN."
            )
        time.sleep(interval)


def stop_simulation_and_clear_checkpoints(sim: Simulation) -> None:
    """Stop the simulation (if running) and delete any checkpoints so the
    node can be safely patched afterwards."""
    sim.refresh()
    if sim.state == "INACTIVE":
        print(f"Simulation {sim.name!r} is already INACTIVE.")
    elif sim.state == "SHUTTING_DOWN":
        print(
            f"Simulation {sim.name!r} is already SHUTTING_DOWN; "
            "waiting for INACTIVE ..."
        )
        wait_for_sim_state(sim, "INACTIVE", timeout=SHUTDOWN_TIMEOUT)
    else:
        print(f"Stopping simulation {sim.name!r} ...")
        sim.shutdown()
        wait_for_sim_state(sim, "INACTIVE", timeout=SHUTDOWN_TIMEOUT)

    checkpoints = list(sim.checkpoints.list())
    if not checkpoints:
        return

    print(f"Clearing {len(checkpoints)} checkpoint(s) before patching the node ...")
    deadline = time.monotonic() + 300
    for cp in checkpoints:
        while True:
            cp.refresh()
            state = getattr(cp, "state", None)
            if state in {"COMPLETE", "DELETED"}:
                break
            if time.monotonic() > deadline:
                raise SystemExit(
                    f"Timed out waiting for checkpoint {cp.id} to become COMPLETE "
                    f"(last state: {state!r})."
                )
            time.sleep(3)
        if getattr(cp, "state", None) == "DELETED":
            continue
        try:
            cp.delete()
            print(f"  deleted checkpoint {cp.id} ({cp.name})")
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"Could not delete checkpoint {cp.id}: {exc}. "
                "Disk state may revert on next start — refusing to continue."
            ) from exc


def start_simulation(sim: Simulation) -> None:
    """Bring the simulation to ACTIVE, waiting out transitional states first.

    Air rejects start() unless the sim is INACTIVE. A previous recover that
    timed out mid-shutdown leaves SHUTTING_DOWN — wait, then start.
    """
    sim.refresh()
    if sim.state == "ACTIVE":
        print(f"Simulation {sim.name!r} is already ACTIVE.")
        return
    if sim.state in {"STARTING", "REBUILDING"}:
        wait_for_sim_state(sim, "ACTIVE", timeout=START_TIMEOUT)
        return
    if sim.state == "SHUTTING_DOWN":
        print(
            f"Simulation {sim.name!r} is SHUTTING_DOWN; "
            "waiting for INACTIVE before start ..."
        )
        wait_for_sim_state(sim, "INACTIVE", timeout=SHUTDOWN_TIMEOUT)
    sim.refresh()
    if sim.state != "INACTIVE":
        raise SystemExit(
            f"Cannot start simulation {sim.name!r} from state {sim.state!r} "
            "(Air requires INACTIVE)."
        )
    print(f"Starting simulation {sim.name!r} ...")
    sim.start()
    wait_for_sim_state(sim, "ACTIVE", timeout=START_TIMEOUT)


def boot_node_to_disk(sim: Simulation, node_name: str | None = None, *, force: bool = False) -> None:
    """Legacy: detach cdrom and set hd-only boot.

    The blank-disk topology pattern (README.md) keeps boot ``["hd", "cdrom"]``
  forever — blank hd falls through to the discovery ISO, and a bootable
    install wins on hd automatically. Stopping the sim to toggle boot/cdrom
    can revert disk state via checkpoints and leaves a non-bootable hd with
    no cdrom fallback ("No bootable device").

    This function is kept for manual recovery only; pass ``force=True`` to run.
    """
    if not force:
        print(
            "Skipping boot-to-disk: blank-disk topology uses permanent boot "
            '["hd", "cdrom"] — no boot/cdrom toggle needed. See README.md. '
            "If the node shows 'No bootable device', run "
            "09_recover_to_discovery.py instead."
        )
        return
    node = get_node(sim, node_name or default_node_name())
    print(f"Current state: node.cdrom={node.cdrom!r} advanced.boot={node.advanced.get('boot')!r}")

    stop_simulation_and_clear_checkpoints(sim)

    print("Setting boot order to hd-only ...")
    advanced = dict(node.advanced or {})
    advanced["boot"] = "hd"
    if not advanced.get("cpu_mode"):
        advanced["cpu_mode"] = "host-passthrough"
    node.update(advanced=advanced)
    node.refresh()
    print(f"  advanced now: {node.advanced}")

    print("Detaching cdrom ...")
    node.update(cdrom=None)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")

    start_simulation(sim)
    print("Node will boot from disk on its next reboot.")


def ensure_jump_host_service(
    sim: Simulation, service_name: str = JUMP_HOST_SERVICE_NAME
) -> tuple[Service, Node]:
    """Idempotently expose oob-mgmt-server's SSH port as an Air Service.

    Reuses an existing SSH service on that interface if one is already
    there (so re-running this doesn't create duplicates), otherwise
    creates one. Returns (service, oob_mgmt_server_node) so callers can
    build the ssh command and know which user to log in as.
    """
    server = get_node(sim, OOB_SERVER_NAME)

    iface = next(
        (i for i in server.interfaces.list() if i.name == OOB_SERVER_INTERFACE), None
    )
    if iface is None:
        raise SystemExit(
            f"No {OOB_SERVER_INTERFACE!r} interface found on node {OOB_SERVER_NAME!r}."
        )

    existing = next((svc for svc in iface.services.list() if svc.node_port == 22), None)
    if existing is not None:
        return existing, server

    service = sim.create_service(
        name=service_name,
        interface=iface,
        dest_port=22,
        service_type="SSH",
    )
    return service, server


def jump_host_ssh_target(service: Service, server: Node) -> tuple[str, int, str]:
    username = getattr(server.image, "default_username", None) or "ubuntu"
    return service.worker_fqdn, service.worker_port, username


def jump_host_ssh_command(service: Service, server: Node) -> str:
    host, port, username = jump_host_ssh_target(service, server)
    return f"ssh -p {port} {username}@{host}"


def jump_host_ssh_probe(
    service: Service,
    server: Node,
    *,
    timeout: int = 20,
) -> tuple[bool, str]:
    """Return (ready, reason). ready=True when non-interactive SSH works."""
    host, port, username = jump_host_ssh_target(service, server)
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=10",
                "-p",
                str(port),
                f"{username}@{host}",
                "echo",
                "jump-host-ok",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)

    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0 and "jump-host-ok" in result.stdout:
        return True, "ok"
    if "password has expired" in combined or "password change required" in combined:
        return False, "password_expired"
    if "connection refused" in combined or "no route to host" in combined:
        return False, "not_reachable"
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"ssh exit {result.returncode}"


def _wait_for_jump_host_ssh(
    service: Service,
    server: Node,
    *,
    timeout: int = 120,
    interval: int = 5,
) -> None:
    deadline = time.monotonic() + timeout
    last_reason = "unknown"
    while time.monotonic() < deadline:
        ready, reason = jump_host_ssh_probe(service, server, timeout=15)
        if ready or reason == "password_expired":
            return
        last_reason = reason
        time.sleep(interval)
    raise SystemExit(
        f"Timed out after {timeout}s waiting for jump host SSH on "
        f"{jump_host_ssh_command(service, server)!r} (last seen: {last_reason!r})."
    )


def bootstrap_jump_host_password(
    service: Service,
    server: Node,
    *,
    initial_password: str | None = None,
    new_password: str | None = None,
    timeout: int = 60,
) -> None:
    """Clear NVIDIA Air's mandatory first-login password change on oob-mgmt-server.

    Fresh oob-mgmt-server VMs ship with default user ``ubuntu`` / password
    ``nvidia`` and refuse to run commands until the password is changed.
    Pubkey auth still connects, but BatchMode SSH fails with
    ``Password change required but no TTY available``.
    """
    ready, reason = jump_host_ssh_probe(service, server, timeout=15)
    if ready:
        print(f"Jump host {OOB_SERVER_NAME!r} already accepts non-interactive SSH.")
        return
    if reason != "password_expired" and reason != "not_reachable":
        print(
            f"Jump host SSH probe: {reason!r} — waiting for SSH to come up before "
            "bootstrapping the password ..."
        )
        _wait_for_jump_host_ssh(service, server, timeout=120)
        ready, reason = jump_host_ssh_probe(service, server, timeout=15)
        if ready:
            print(f"Jump host {OOB_SERVER_NAME!r} already accepts non-interactive SSH.")
            return

    if shutil.which("expect") is None:
        raise SystemExit(
            "The `expect` command is required to bootstrap the jump host password. "
            "Install expect (e.g. `sudo dnf install expect`) or run the password "
            "change manually, then re-run this script."
        )

    host, port, username = jump_host_ssh_target(service, server)
    initial = initial_password or env_config.jump_host_initial_password(
        image_default=getattr(server.image, "default_password", None)
    )
    new = new_password or env_config.jump_host_password()

    print(
        f"Bootstrapping jump host password for {username}@{host}:{port} "
        f"(factory password -> JUMP_HOST_PASSWORD) ..."
    )

    expect_script = r"""
set timeout [expr {$env(JUMP_HOST_EXPECT_TIMEOUT)}]
set initial $env(JUMP_HOST_INITIAL_PASSWORD)
set newpass $env(JUMP_HOST_PASSWORD)
set port $env(JUMP_HOST_PORT)
set user $env(JUMP_HOST_USER)
set host $env(JUMP_HOST_HOST)

spawn ssh -tt -o StrictHostKeyChecking=accept-new -p $port $user@$host

expect {
    -re "(?i)are you sure you want to continue connecting" {
        send "yes\r"
        exp_continue
    }
    -re "(?i)current password:" {
        send "$initial\r"
    }
    -re "(?i)password:" {
        send "$initial\r"
    }
    timeout {
        puts "expect timed out waiting for current/login password prompt"
        exit 1
    }
    eof {
        puts "ssh closed before password prompts appeared"
        exit 1
    }
}

expect {
    -re "New password:" {
        send "$newpass\r"
    }
    timeout {
        puts "expect timed out waiting for new password prompt"
        exit 1
    }
    eof {
        puts "ssh closed before new password prompt"
        exit 1
    }
}

expect {
    -re "Retype new password:" {
        send "$newpass\r"
    }
    timeout {
        puts "expect timed out waiting for retype password prompt"
        exit 1
    }
    eof {
        puts "ssh closed before retype password prompt"
        exit 1
    }
}

expect {
    -re "password updated successfully" {
        exit 0
    }
    -re "jump-host-bootstrap-ok" {
        exit 0
    }
    -re {ubuntu@.*[$#] } {
        send "echo jump-host-bootstrap-ok\r"
        exp_continue
    }
    -re {\$ $} {
        send "echo jump-host-bootstrap-ok\r"
        exp_continue
    }
    timeout {
        puts "expect timed out waiting for password change confirmation"
        exit 1
    }
    eof {
        exit 0
    }
}
"""
    env = os.environ.copy()
    env.update(
        {
            "JUMP_HOST_INITIAL_PASSWORD": initial,
            "JUMP_HOST_PASSWORD": new,
            "JUMP_HOST_PORT": str(port),
            "JUMP_HOST_USER": username,
            "JUMP_HOST_HOST": host,
            "JUMP_HOST_EXPECT_TIMEOUT": str(timeout),
        }
    )
    result = subprocess.run(
        ["expect", "-c", expect_script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout + 30,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(
            "Jump host password bootstrap failed. "
            f"Try connecting manually with:\n\n    {jump_host_ssh_command(service, server)}\n\n"
            f"Factory password is the image default (usually {env_config.DEFAULT_JUMP_HOST_INITIAL_PASSWORD!r}); "
            f"set JUMP_HOST_PASSWORD for the new password. "
            f"Details: {detail or 'expect exited non-zero'}"
        )

    ready, reason = jump_host_ssh_probe(service, server, timeout=15)
    if not ready:
        raise SystemExit(
            "Jump host password bootstrap appeared to succeed, but non-interactive "
            f"SSH still fails: {reason!r}"
        )
    print(f"Jump host {OOB_SERVER_NAME!r} is ready for non-interactive SSH.")


def ensure_jump_host_ready(
    sim: Simulation,
    service_name: str = JUMP_HOST_SERVICE_NAME,
    *,
    skip_bootstrap: bool = False,
) -> tuple[Service, Node]:
    """Expose oob-mgmt-server SSH and clear the first-login password if needed."""
    service, server = ensure_jump_host_service(sim, service_name=service_name)
    if not skip_bootstrap:
        bootstrap_jump_host_password(service, server)
    return service, server

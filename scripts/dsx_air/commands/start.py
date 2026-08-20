from __future__ import annotations

from dsx_air._bootstrap import ensure_scripts_path
from dsx_air.output import Report

ensure_scripts_path()

import air_common  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402


def run_start(*, report: Report | None = None) -> int:
    out = report or Report()
    out.section("Start simulation")

    api = get_api()
    sim = air_common.get_simulation(api)
    out.kv("simulation", sim.name)
    out.kv("state (before)", sim.state)

    if sim.state == "INACTIVE":
        out.line("  Starting simulation ...")
        sim.start()
        air_common.wait_for_sim_state(sim, "ACTIVE", timeout=600)
    elif sim.state == "ACTIVE":
        out.line("  Simulation already ACTIVE.")
    else:
        out.warn(f"Simulation state is {sim.state!r}; wait or check Air UI.")
        return out.finish()

    sim.refresh()
    out.kv("state (after)", sim.state)

    out.section("Jump host")
    service, server = air_common.ensure_jump_host_ready(sim)
    ssh = air_common.jump_host_ssh_command(service, server)
    out.kv("ssh", ssh)
    ready, reason = air_common.jump_host_ssh_probe(service, server, timeout=15)
    out.kv("ready", "yes" if ready else f"no ({reason})")
    if not ready:
        out.warn(f"Jump host not ready: {reason}")

    return out.finish()

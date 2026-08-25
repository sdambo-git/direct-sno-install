from __future__ import annotations

from pathlib import Path

from dsx_air._bootstrap import ensure_scripts_path
from dsx_air.output import Report
from dsx_air.spec import activate_spec

ensure_scripts_path()

import air_common  # noqa: E402
import env_config  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402


def run_start(*, spec_path: Path | None = None, report: Report | None = None) -> int:
    activate_spec(spec_path)
    out = report or Report()
    out.section("Start simulation")

    api = get_api()
    sim = air_common.get_simulation(api, env_config.simulation_name())
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
    try:
        air_common.ensure_jump_host_cluster_dns(service, server)
        out.kv("cluster_dns", "jump host /etc/hosts updated")
    except SystemExit as exc:
        out.warn(str(exc))

    return out.finish()

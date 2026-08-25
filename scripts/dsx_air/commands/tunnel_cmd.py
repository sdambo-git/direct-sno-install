from __future__ import annotations

import sys
from pathlib import Path

from dsx_air import air_status, tunnel
from dsx_air._bootstrap import ensure_scripts_path
from dsx_air.air_status import AirLookupError
from dsx_air.spec import activate_spec

ensure_scripts_path()
import air_common  # noqa: E402
import env_config  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402


def run_tunnel(*, check: bool = False, spec_path: Path | None = None) -> int:
    activate_spec(spec_path)
    profile = air_status.profile_info()
    jump_target: tunnel.JumpTarget | None = None
    jump: dict[str, str] = {}
    try:
        api = get_api()
        sim = air_common.get_simulation(api, env_config.simulation_name())
        jump = air_status.jump_host_info(sim=sim)
        jump_target = air_status.jump_target_from_info(jump)
    except (AirLookupError, SystemExit) as exc:
        print(exc, file=sys.stderr)
        return 1

    if jump_target is None:
        print(
            jump.get("reason")
            or "Jump host is not ready. Pass --spec examples/ha-3cp-2w.yaml "
            "and run dsx-air start if needed.",
            file=sys.stderr,
        )
        return 1

    cmd = tunnel.build_tunnel_command(target=jump_target, api_vip=profile["api_vip"])
    print(cmd)

    if not check:
        return 0

    reachable, reason = tunnel.api_reachable()
    if reachable:
        print("API probe: ok (https://127.0.0.1:6443/version)")
        return 0

    print(f"API probe: failed ({reason})")
    print("Start the tunnel command above in another terminal, then retry --check.")
    return 1

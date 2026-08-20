from __future__ import annotations

from dsx_air import air_status, kubeconfig, oc_checks, tunnel
from dsx_air.air_status import AirLookupError
from dsx_air.output import Report


def run_tunnel(*, check: bool = False) -> int:
    profile = air_status.profile_info()
    jump_ssh = ""
    try:
        jump = air_status.jump_host_info()
        jump_ssh = jump["ssh"]
    except AirLookupError as exc:
        print(f"Warning: {exc}")
        jump_ssh = "<set AIR_API_KEY and ensure sim ACTIVE>"

    cmd = tunnel.build_tunnel_command(jump_ssh=jump_ssh, api_vip=profile["api_vip"])
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

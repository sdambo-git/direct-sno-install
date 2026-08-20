from __future__ import annotations

import sys

from dsx_air import air_status, tunnel
from dsx_air.air_status import AirLookupError

_MISSING_KEY_MSG = (
    "Set AIR_API_KEY (Ami org) to resolve jump host port from Air API."
)


def run_tunnel(*, check: bool = False) -> int:
    profile = air_status.profile_info()
    jump_target: tunnel.JumpTarget | None = None
    try:
        jump = air_status.jump_host_info()
        jump_target = air_status.jump_target_from_info(jump)
    except AirLookupError as exc:
        print(exc, file=sys.stderr)
        return 1

    if jump_target is None:
        print(_MISSING_KEY_MSG, file=sys.stderr)
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

#!/usr/bin/env python3
"""
Read-only: dumps the raw API response for the 'sno-cluster' simulation to
look for validation error details the SDK model doesn't surface.
"""
from __future__ import annotations

from upload_discovery_iso import get_api


def main() -> None:
    api = get_api()

    sims = list(api.simulations.list(search="sno-cluster"))
    if not sims:
        print("No simulation named 'sno-cluster' found.")
        return

    sim = sims[0]
    print(f"Simulation id={sim.id} state={sim.state!r}")

    from air_sdk import const
    resp = api.client.get(f"{const.AIR_API_URL}/simulations/{sim.id}/")
    print()
    print("--- Raw response ---")
    print(resp.status_code)
    print(resp.text)


if __name__ == "__main__":
    main()

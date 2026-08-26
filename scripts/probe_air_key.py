#!/usr/bin/env python3
"""
Check that AIR_API_KEY (NGC scoped / Air service key) can talk to Air and
create a host.

Does **not** touch the lab simulation (`ocp-cluster` / `sno-cluster`). It
creates a throwaway simulation, adds one small node, then deletes both
unless --keep.

    export AIR_API_KEY=nvapi-...    # NGC Personal or Service key (not kubeadmin)
    # leave AIR_API_URL unset for public DSX Air (api.air-ngc.nvidia.com)
    uv run probe_air_key.py
    uv run probe_air_key.py --create-host
    uv run probe_air_key.py --create-host --keep
"""
from __future__ import annotations

import argparse
import sys
import time

from air_sdk import const
from air_common import get_api
import env_config

PROBE_SIM = "air-key-probe"
PROBE_NODE = "probe-host"
IMAGE_CANDIDATES = ("generic/ubuntu2204", "ubuntu-22.04", "centos9")


def _find_image(api):
    for name in IMAGE_CANDIDATES:
        images = [i for i in api.images.list(search=name) if i.name == name]
        if images:
            return images[0]
    raise SystemExit(
        "No catalog image found among: " + ", ".join(IMAGE_CANDIDATES)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-host",
        action="store_true",
        help="Create a throwaway simulation + one node (then delete unless --keep).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the probe simulation in Air instead of deleting it.",
    )
    args = parser.parse_args()

    print("Authenticating with AIR_API_KEY / AIR_API_KEY_FILE ...")
    print("  (This is NGC Air identity, not OpenShift kubeadmin.)")
    key = env_config.air_api_key()
    api_url = env_config.air_api_url() or const.AIR_API_URL
    org = env_config.air_ngc_org()
    print(f"  API URL: {api_url}")
    print(f"  NGC org header: {org or '(not set; key org is used)'}")
    print(f"  key length: {len(key)}")
    print(f"  starts with nvapi-: {key.startswith('nvapi-')}")
    if not key.startswith("nvapi-"):
        raise SystemExit(
            "This is not an NGC Scoped API Key (SAK). DSX Air only accepts keys "
            "that start with 'nvapi-'. Create a Personal API Key at "
            "https://org.ngc.nvidia.com/setup/api-keys with NVIDIA Air under "
            "Services Included. NGC 'service keys' / legacy hex keys fail with "
            "'Failed to authenticate user from NGC headers'."
        )
    api = get_api()
    sims = list(api.simulations.list())
    print(f"OK: listed {len(sims)} simulation(s):")
    for sim in sims[:20]:
        print(f"  - {sim.name!r}  state={sim.state!r}  id={sim.id}")
    if len(sims) > 20:
        print(f"  ... {len(sims) - 20} more")

    if not args.create_host:
        print("\nRead access works. Re-run with --create-host to also create a node.")
        return

    existing = [s for s in sims if s.name == PROBE_SIM]
    for sim in existing:
        print(f"Deleting leftover probe simulation {sim.id} ...")
        sim.delete()

    image = _find_image(api)
    print(f"Using catalog image {image.name!r} (id={image.id})")
    print(f"Creating simulation {PROBE_SIM!r} ...")
    sim = api.simulations.create(name=PROBE_SIM)
    print(f"  sim id={sim.id} state={sim.state!r}")
    print(f"Creating node {PROBE_NODE!r} ...")
    node = api.nodes.create(
        simulation=sim,
        image=image,
        name=PROBE_NODE,
        cpu=1,
        memory=1024,
        storage=10,
    )
    print(f"  node id={node.id} name={node.name!r} state={node.state!r}")
    print("Create-host succeeded.")

    if args.keep:
        print(f"Kept {PROBE_SIM!r} in Air (--keep). Delete it in the UI when done.")
        return

    print(f"Deleting probe simulation {sim.id} ...")
    sim.delete()
    time.sleep(1)
    leftover = [s for s in api.simulations.list(search=PROBE_SIM) if s.name == PROBE_SIM]
    if leftover:
        print(f"Warning: {PROBE_SIM!r} still listed after delete.")
    else:
        print("Probe simulation deleted.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        message = env_config.describe_error(exc)
        if "NGC headers" in message:
            message += (
                " Air did not accept this token as an NGC Scoped API Key. "
                "Use a Personal API Key from org.ngc.nvidia.com/setup/api-keys "
                "(must start with nvapi- and include NVIDIA Air). Do not use an "
                "NGC service-account key, legacy hex API key, or a quoted value."
            )
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(1) from exc

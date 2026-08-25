#!/usr/bin/env python3
"""
Recover topology node(s) for discovery after a failed install or "No bootable device".

Uses the blank-disk pattern from README.md:
  - boot stays ["hd", "cdrom"] (blank hd falls through to discovery ISO)
  - node.rebuild() wipes the disk back to blank-100g
  - discovery ISO re-attached on cdrom

Rebuilds every requested node first, then one simulation stop/start to attach
cdroms (a 3-node HA sim takes several minutes to shut down — do not recover
nodes one-by-one with three separate processes).

Optionally resets the Assisted Installer cluster so hosts can re-register.

    uv run 09_recover_to_discovery.py
    uv run 09_recover_to_discovery.py --node ocp-cp-1
    uv run 09_recover_to_discovery.py --all --reset-ai
    uv run 09_recover_to_discovery.py --node ocp-cp-0 --node ocp-cp-1 --node ocp-cp-2 --reset-ai
"""
from __future__ import annotations

import argparse
import sys

from air_common import (
    default_node_name,
    get_api,
    get_node,
    get_simulation,
    start_simulation,
    stop_simulation_and_clear_checkpoints,
    wait_for_sim_state,
)
import env_config


def _find_image_by_name(api, name: str):
    return next(
        (img for img in api.images.list(search=name) if img.name == name),
        None,
    )


def _attach_discovery_cdrom(node, image) -> None:
    advanced = dict(node.advanced or {})
    advanced["boot"] = ["hd", "cdrom"]
    advanced["cpu_mode"] = "host-passthrough"
    print(f"Attaching cdrom {image.id} on {node.name!r} boot={advanced['boot']!r} ...")
    node.update(cdrom={"image": image.id}, advanced=advanced)
    node.refresh()
    print(f"  cdrom={node.cdrom!r} boot={node.advanced.get('boot')!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node",
        action="append",
        dest="nodes",
        help="Topology node to recover. Repeat for multiple nodes "
        "(preferred over three separate runs).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Recover every node in the topology manifest.",
    )
    parser.add_argument(
        "--reset-ai",
        action="store_true",
        help="Also reset the Assisted Installer cluster (ai.stop_cluster).",
    )
    args = parser.parse_args()

    if args.all:
        node_names = env_config.topology_node_names()
        if not node_names:
            raise SystemExit("No topology nodes found; cannot use --all.")
    elif args.nodes:
        node_names = args.nodes
    else:
        node_names = [default_node_name()]

    api = get_api()
    sim = get_simulation(api)

    images_by_node = {}
    for node_name in node_names:
        image_name = env_config.node_cdrom_image(node_name)
        image = _find_image_by_name(api, image_name)
        if image is None:
            raise SystemExit(
                f"No discovery ISO image found (tried {image_name!r}). "
                "Run 00_create_discovery_iso.py and upload_discovery_iso.py first."
            )
        images_by_node[node_name] = image

    print(
        f"Recovering {len(node_names)} node(s) in {sim.name!r}: "
        f"{', '.join(node_names)}"
    )
    start_simulation(sim)

    for node_name in node_names:
        node = get_node(sim, node_name)
        image = images_by_node[node_name]
        print(
            f"Rebuilding {node.name!r} (blank disk + later cdrom "
            f"{image.name!r}) ..."
        )
        node.rebuild()
        node.refresh()
        wait_for_sim_state(sim, "ACTIVE", timeout=300)

    stop_simulation_and_clear_checkpoints(sim)

    for node_name in node_names:
        _attach_discovery_cdrom(get_node(sim, node_name), images_by_node[node_name])

    start_simulation(sim)

    if args.reset_ai:
        from assisted_common import get_client

        ai = get_client(quiet=False)
        name = env_config.cluster_name()
        print(f"Resetting Assisted Installer cluster {name!r} ...")
        try:
            ai.stop_cluster(name)
        except Exception as exc:  # noqa: BLE001
            if "409" not in str(exc):
                raise
            print(f"  skip stop_cluster (cluster not in resettable state): {exc}")
        print("Restarting simulation so nodes boot discovery ISO after AI reset ...")
        stop_simulation_and_clear_checkpoints(sim)
        start_simulation(sim)

    print(
        f"\nNode(s) {', '.join(node_names)} should boot discovery ISO "
        "(blank hd → fall through to cdrom). "
        "Next: uv run scripts/06_wait_for_host_ipv4.py"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

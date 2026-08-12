#!/usr/bin/env python3
"""
Recover sno-cluster for discovery after a failed install or "No bootable device".

Uses the blank-disk pattern from README.md:
  - boot stays ["hd", "cdrom"] (blank hd falls through to discovery ISO)
  - node.rebuild() wipes the disk back to blank-100g
  - discovery ISO re-attached on cdrom

Optionally resets the Assisted Installer cluster so the host can re-register.

    uv run 09_recover_to_discovery.py
    uv run 09_recover_to_discovery.py --reset-ai
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from air_common import (
    get_api,
    get_node,
    get_simulation,
    start_simulation,
    stop_simulation_and_clear_checkpoints,
    wait_for_sim_state,
)
from upload_discovery_iso import IMAGE_NAME


def _find_image_by_name(api, name: str):
    return next(
        (img for img in api.images.list(search=name) if img.name == name),
        None,
    )


def _discovery_image_name() -> str:
    """Prefer topology.json cdrom name, then env default."""
    topo_path = Path(__file__).resolve().parent.parent / "topology.json"
    if topo_path.is_file():
        topo = json.loads(topo_path.read_text())
        node = topo.get("content", {}).get("nodes", {}).get("sno-cluster", {})
        cdrom = node.get("cdrom")
        if cdrom:
            return str(cdrom)
    return IMAGE_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset-ai",
        action="store_true",
        help="Also reset the Assisted Installer cluster (ai.stop_cluster).",
    )
    args = parser.parse_args()

    api = get_api()
    sim = get_simulation(api)
    node = get_node(sim)

    image_name = _discovery_image_name()
    image = _find_image_by_name(api, image_name)
    if image is None:
        raise SystemExit(
            f"No discovery ISO image found (tried {image_name!r}). "
            "Run 00_create_discovery_iso.py and upload_discovery_iso.py first."
        )

    print(f"Recovering {node.name!r}: rebuild blank disk + cdrom {image_name!r} ...")
    if sim.state != "ACTIVE":
        start_simulation(sim)

    node.rebuild()
    node.refresh()
    wait_for_sim_state(sim, "ACTIVE", timeout=300)

    stop_simulation_and_clear_checkpoints(sim)

    advanced = dict(node.advanced or {})
    advanced["boot"] = ["hd", "cdrom"]
    advanced["cpu_mode"] = "host-passthrough"
    print(f"Attaching cdrom {image.id} boot={advanced['boot']!r} ...")
    node.update(cdrom={"image": image.id}, advanced=advanced)
    node.refresh()
    print(f"  cdrom={node.cdrom!r} boot={node.advanced.get('boot')!r}")

    start_simulation(sim)

    if args.reset_ai:
        import env_config
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
        # Host must boot the discovery ISO to finish reset — restart so blank hd
        # falls through to cdrom even if the node was already running.
        print("Restarting simulation so node boots discovery ISO after AI reset ...")
        stop_simulation_and_clear_checkpoints(sim)
        start_simulation(sim)

    print(
        "\nNode should boot discovery ISO (blank hd → fall through to cdrom). "
        "Next: uv run scripts/06_wait_for_host_ipv4.py"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

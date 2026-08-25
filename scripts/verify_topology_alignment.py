#!/usr/bin/env python3
"""
Read-only sanity check: confirms the images topology references
(`cdrom` discovery ISO and `os` blank disk) exist in Air.
Makes no changes.
"""
from __future__ import annotations

import sys

from upload_discovery_iso import get_api
import env_config


def _check_image(api, label: str, name: str) -> bool:
    print(f"--- Looking for {label} image {name!r} ---")
    found = False
    for img in api.images.list(search=name):
        if img.name == name:
            found = True
            print(
                f"  name={img.name!r} id={img.id} "
                f"upload_status={getattr(img, 'upload_status', None)}"
            )
    print("MATCH" if found else "NO EXACT NAME MATCH FOUND")
    print()
    return found


def main() -> None:
    api = get_api()
    topology = env_config.topology_path()
    nodes = env_config.topology_node_names()
    cdrom_names = {
        env_config.node_cdrom_image(node_name) for node_name in nodes
    } if nodes else {env_config.DEFAULT_DISCOVERY_ISO_NAME}

    print(f"Topology: {topology}")
    print(f"Nodes: {nodes or '(default)'}")
    print()

    ok = True
    ok &= _check_image(api, "os", env_config.DEFAULT_BLANK_IMAGE_NAME)
    for cdrom_name in sorted(cdrom_names):
        ok &= _check_image(api, "cdrom", cdrom_name)

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

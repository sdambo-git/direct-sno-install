#!/usr/bin/env python3
"""
Read-only sanity check: confirms the images topology.json references
(`dsxair-discovery-iso` cdrom and `blank-100g` os) exist in Air.
Makes no changes.
"""
from __future__ import annotations

from upload_discovery_iso import get_api
import env_config


def main() -> None:
    api = get_api()

    for label, name in (
        ("cdrom", env_config.DEFAULT_DISCOVERY_ISO_NAME),
        ("os", env_config.DEFAULT_BLANK_IMAGE_NAME),
    ):
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


if __name__ == "__main__":
    main()

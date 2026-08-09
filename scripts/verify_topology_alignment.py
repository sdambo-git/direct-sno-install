#!/usr/bin/env python3
"""
Read-only sanity check: confirms the image topology.json's "cdrom" field
references (sno-discovery-iso) actually exists in Air with a matching name,
and looks up whether "rhel" (topology.json's "os" field) resolves to a
real catalog image too. Makes no changes.
"""
from __future__ import annotations

from upload_discovery_iso import API_KEY, IMAGE_NAME, get_api  # reuse existing config


def main() -> None:
    api = get_api()

    print(f"--- Looking for cdrom image '{IMAGE_NAME}' ---")
    found = False
    for img in api.images.list(search=IMAGE_NAME):
        found = found or img.name == IMAGE_NAME
        print(f"  name={img.name!r} id={img.id} cpu_arch={getattr(img, 'cpu_arch', None)}")
    print("MATCH" if found else "NO EXACT NAME MATCH FOUND")

    print()
    print("--- Looking for 'rhel' (topology.json's \"os\" field) ---")
    for img in api.images.list(search="rhel"):
        print(f"  name={img.name!r} id={img.id} cpu_arch={getattr(img, 'cpu_arch', None)}")


if __name__ == "__main__":
    main()

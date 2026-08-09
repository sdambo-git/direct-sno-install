#!/usr/bin/env python3
"""
Step 2 of ../README.md — upload the Assisted Installer discovery ISO to
NVIDIA Air as an image, so it exists in your org *before* you import
topology.json (whose "cdrom" field references it by name).

Install deps:
    pip install nv-air-sdk

Fill in API_KEY below (or export AIR_API_KEY instead and leave API_KEY as
None), double check ISO_PATH points at wherever your browser actually saved
the discovery ISO from console.redhat.com, then run:

    python upload_discovery_iso.py
"""
from __future__ import annotations

import os

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

# --- Fill these in -----------------------------------------------------

# Do not hardcode a real key here — this file lives in the repo. Set
# AIR_API_KEY in your shell environment instead (e.g. `export
# AIR_API_KEY=$(cat ~/.air_api_key.txt)`).
API_KEY = None  # e.g. "nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                # leave None to fall back to the AIR_API_KEY env var below

ISO_PATH = "/home/sdambo/Downloads/dsxair-discovery.iso"

IMAGE_NAME = "dsxair-discovery-iso"  # must match "cdrom" in ../topology.json

# -------------------------------------------------------------------------


def get_api() -> AirApi:
    api_key = API_KEY or os.environ.get("AIR_API_KEY")
    if not api_key:
        raise SystemExit(
            "No API key set. Fill in API_KEY at the top of this script, or "
            "export AIR_API_KEY=nvapi-... before running."
        )
    return AirApi.with_api_key(api_key=api_key)


def main() -> None:
    api = get_api()

    print(f"Uploading {ISO_PATH} as Air image '{IMAGE_NAME}' ...")
    image = api.images.create(
        name=IMAGE_NAME,
        version="2.0.0",
        default_username="core",
        default_password="password",  # cosmetic; API rejects blank
        cpu_arch="x86",
        filepath=ISO_PATH,
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")
    print(
        "Now import ../topology.json (its \"cdrom\" field already references "
        f"'{IMAGE_NAME}') and continue with Step 3."
    )


if __name__ == "__main__":
    main()

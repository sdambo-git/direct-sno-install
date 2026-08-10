#!/usr/bin/env python3
"""
Standalone script — copy this to whichever machine actually holds the
pre-installed SNO qcow2 file and run it there directly. Uploads a qcow2
disk image to NVIDIA Air as a VM image, so it can be referenced as a
node's *root disk* (the node's `image` field) instead of a `cdrom`.

Since the resulting node boots straight from an already fully-installed
disk, there's no discovery ISO, no boot-order dance, and no reboot-loop
risk like the Assisted Installer discovery-ISO flow has.

Install deps (only nv-air-sdk is required, no other system packages):
    pip install nv-air-sdk

Fill in the config below, or set these environment variables instead:
    AIR_API_KEY, QCOW2_PATH, IMAGE_NAME

Then run:
    python upload_qcow2_image.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

# --- Fill these in, or set the equivalent env vars instead --------------

API_KEY = None  # e.g. "nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                # leave None to fall back to AIR_API_KEY / API_KEY_FILE below

API_KEY_FILE = "~/.air_api_key.txt"  # used if API_KEY is None and AIR_API_KEY isn't set

QCOW2_PATH = None  # e.g. "/data/sno-installed.qcow2"
                    # leave None to require QCOW2_PATH env var

IMAGE_NAME = "sno-installed-qcow2"  # what this image is called inside Air;
                                     # you'll reference this name when
                                     # creating the node

IMAGE_VERSION = "1.0.0"

# RHCOS's `core` user is SSH-key-only; these are cosmetic metadata Air
# requires to be non-blank, they're not used for real authentication.
DEFAULT_USERNAME = "core"
DEFAULT_PASSWORD = "not-used-sno-disk"

CPU_ARCH = "x86"  # or "ARM" if applicable

# -------------------------------------------------------------------------


def get_api() -> AirApi:
    api_key = API_KEY or os.environ.get("AIR_API_KEY")
    if not api_key:
        key_file = Path(API_KEY_FILE).expanduser()
        if key_file.is_file():
            api_key = key_file.read_text().strip()
    if not api_key:
        raise SystemExit(
            "No API key set. Fill in API_KEY at the top of this script, "
            "export AIR_API_KEY=nvapi-..., or save it to "
            f"{API_KEY_FILE} before running."
        )
    return AirApi.with_api_key(api_key=api_key)


def main() -> None:
    qcow2_path = QCOW2_PATH or os.environ.get("QCOW2_PATH")
    if not qcow2_path:
        raise SystemExit(
            "No qcow2 path set. Fill in QCOW2_PATH at the top of this "
            "script, or export QCOW2_PATH=/path/to/file.qcow2 before running."
        )
    if not os.path.isfile(qcow2_path):
        raise SystemExit(f"File not found: {qcow2_path}")

    size_gb = os.path.getsize(qcow2_path) / (1024 ** 3)
    print(f"Uploading {qcow2_path} ({size_gb:.1f} GB) as Air image {IMAGE_NAME!r} ...")
    print("This can take a while for a multi-GB disk image — it uploads in "
          "~100MB multipart chunks.")

    api = get_api()
    image = api.images.create(
        name=IMAGE_NAME,
        version=IMAGE_VERSION,
        default_username=DEFAULT_USERNAME,
        default_password=DEFAULT_PASSWORD,
        cpu_arch=CPU_ARCH,
        provider="VM",
        filepath=qcow2_path,
        max_workers=4,  # parallel upload workers; increase if you have bandwidth to spare
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")
    print(
        f"\nNext: create a node using image={IMAGE_NAME!r} directly (as the "
        "node's root disk, not a cdrom) instead of the generic 'rhel' "
        "catalog image. No discovery ISO or boot-order config needed for "
        "this node."
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Standalone script — uploads a qcow2 disk image to NVIDIA Air as a VM image,
so it can be referenced as a node's root disk (the node's `image` field)
instead of a `cdrom`.

Also used as the underlying pattern for blank-100g uploads; prefer
upload_blank_disk.py for the Assisted Installer blank-disk boot path.

Requires AIR_API_KEY (or AIR_API_KEY_FILE) and QCOW2_PATH.

    QCOW2_PATH=/path/to/disk.qcow2 uv run upload_qcow2_image.py
"""
from __future__ import annotations

import os

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

import env_config

IMAGE_NAME = os.environ.get("IMAGE_NAME", "sno-installed-qcow2")
IMAGE_VERSION = "1.0.0"
DEFAULT_USERNAME = "core"
DEFAULT_PASSWORD = "not-used-sno-disk"
CPU_ARCH = "x86"


def get_api() -> AirApi:
    return AirApi.with_api_key(api_key=env_config.air_api_key())


def main() -> None:
    qcow2_path = os.environ.get("QCOW2_PATH")
    if not qcow2_path:
        raise SystemExit(
            "No qcow2 path set. Export QCOW2_PATH=/path/to/file.qcow2 before running."
        )
    if not os.path.isfile(qcow2_path):
        raise SystemExit(f"File not found: {qcow2_path}")

    size_gb = os.path.getsize(qcow2_path) / (1024**3)
    print(f"Uploading {qcow2_path} ({size_gb:.1f} GB) as Air image {IMAGE_NAME!r} ...")
    print(
        "This can take a while for a multi-GB disk image — it uploads in "
        "~100MB multipart chunks."
    )

    api = get_api()
    image = api.images.create(
        name=IMAGE_NAME,
        version=IMAGE_VERSION,
        default_username=DEFAULT_USERNAME,
        default_password=DEFAULT_PASSWORD,
        cpu_arch=CPU_ARCH,
        provider="VM",
        filepath=qcow2_path,
        max_workers=4,
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Create a sparse blank 100G qcow2 and upload it to NVIDIA Air as `blank-100g`
(the topology.json "os" image for the Assisted Installer blank-disk boot
pattern).

Requires AIR_API_KEY (or AIR_API_KEY_FILE) and `qemu-img` on PATH.

    uv run upload_blank_disk.py
    uv run upload_blank_disk.py --replace
"""
from __future__ import annotations

import argparse
import os
import subprocess

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

import env_config

IMAGE_NAME = env_config.DEFAULT_BLANK_IMAGE_NAME
IMAGE_VERSION = "1.0.0"
DISK_SIZE = "100G"


def get_api() -> AirApi:
    return AirApi.with_api_key(api_key=env_config.air_api_key())


def _find_image(api: AirApi):
    return next(
        (img for img in api.images.list(search=IMAGE_NAME) if img.name == IMAGE_NAME),
        None,
    )


def _ensure_blank_qcow2(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 0:
        print(f"Reusing local blank disk {path}")
        return
    print(f"Creating sparse blank qcow2 {path} ({DISK_SIZE}) ...")
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", str(path), DISK_SIZE],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the file content of an existing Air image with the same name.",
    )
    args = parser.parse_args()

    qcow2_path = env_config.blank_qcow2_path()
    _ensure_blank_qcow2(qcow2_path)

    api = get_api()
    existing = _find_image(api)
    if existing is not None and not args.replace:
        print(
            f"Air image {IMAGE_NAME!r} already exists (id={existing.id}, "
            f"upload_status={existing.upload_status!r}). Skipping upload. "
            "Pass --replace to overwrite its content."
        )
        return

    size_gb = os.path.getsize(qcow2_path) / (1024**3)
    if existing is not None and args.replace:
        print(
            f"Replacing content of existing Air image {IMAGE_NAME!r} "
            f"(id={existing.id}) with {qcow2_path} ({size_gb:.3f} GB on disk) ..."
        )
        existing.clear_upload()
        existing.refresh()
        existing.upload(filepath=str(qcow2_path))
        wait_for_state(existing, "COMPLETE", state_field="upload_status", error_states="READY")
        print(f"Replace complete: image id={existing.id}, name={existing.name!r}")
        return

    print(
        f"Uploading {qcow2_path} ({size_gb:.3f} GB on disk) as Air image "
        f"{IMAGE_NAME!r} ..."
    )
    image = api.images.create(
        name=IMAGE_NAME,
        version=IMAGE_VERSION,
        default_username="core",
        default_password="not-used-blank-disk",
        cpu_arch="x86",
        provider="VM",
        filepath=str(qcow2_path),
        max_workers=4,
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")
    print("Next: run 01_create_simulation.py to import topology.json.")


if __name__ == "__main__":
    main()

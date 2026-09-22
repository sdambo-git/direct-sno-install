#!/usr/bin/env python3
"""
Create a sparse blank qcow2 and upload it to NVIDIA Air.

Default (HA / small labs): 100G image `blank-100g`.

    uv run upload_blank_disk.py
    uv run upload_blank_disk.py --replace

SNO / IBI-sized disk (also: upload_blank_disk_sno.py):

    uv run upload_blank_disk.py --name blank-300g --size 300G

Requires AIR_API_KEY (or AIR_API_KEY_FILE) and `qemu-img` on PATH.
"""
from __future__ import annotations

import argparse
import os
import subprocess

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

import env_config

IMAGE_VERSION = "1.0.0"


def get_api() -> AirApi:
    return AirApi.with_api_key(api_key=env_config.air_api_key())


def _find_image(api: AirApi, name: str):
    return next(
        (img for img in api.images.list(search=name) if img.name == name),
        None,
    )


def _ensure_blank_qcow2(path, *, size: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 0:
        print(f"Reusing local blank disk {path}")
        return
    print(f"Creating sparse blank qcow2 {path} ({size}) ...")
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", str(path), size],
        check=True,
    )


def run_upload(*, name: str, size: str, replace: bool = False) -> None:
    qcow2_path = env_config.blank_qcow2_path(image_name=name)
    _ensure_blank_qcow2(qcow2_path, size=size)

    api = get_api()
    existing = _find_image(api, name)
    if existing is not None and not replace:
        print(
            f"Air image {name!r} already exists (id={existing.id}, "
            f"upload_status={existing.upload_status!r}). Skipping upload. "
            "Pass --replace to overwrite its content."
        )
        return

    size_gb = os.path.getsize(qcow2_path) / (1024**3)
    if existing is not None and replace:
        print(
            f"Replacing content of existing Air image {name!r} "
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
        f"{name!r} ({size} virtual) ..."
    )
    image = api.images.create(
        name=name,
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        default=env_config.DEFAULT_BLANK_IMAGE_NAME,
        help=f"Air image name (default: {env_config.DEFAULT_BLANK_IMAGE_NAME}).",
    )
    parser.add_argument(
        "--size",
        default="100G",
        help="qemu-img virtual size (default: 100G). Sparse on disk.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the file content of an existing Air image with the same name.",
    )
    args = parser.parse_args(argv)
    run_upload(name=args.name, size=args.size, replace=args.replace)


if __name__ == "__main__":
    main()

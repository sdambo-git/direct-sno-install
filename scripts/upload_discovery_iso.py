#!/usr/bin/env python3
"""
Upload the Assisted Installer discovery ISO to NVIDIA Air as an image named
`dsxair-discovery-iso` (must match topology.json's "cdrom" field) *before*
importing the topology.

Requires AIR_API_KEY (or AIR_API_KEY_FILE) and a local ISO via
DISCOVERY_ISO_PATH / ISO_PATH (default: ../.cache/dsxair-discovery.iso).

There is no --replace: Air rejects clearing/overwriting an image's content
once it's attached to a node ("This image is currently associated with
nodes."), and even when unattached it can serve a stale CDROM cache. Upload
under a new --name instead and repoint topology `cdrom` fields at it.

    uv run upload_discovery_iso.py
    uv run upload_discovery_iso.py --name dsxair-discovery-iso-current
"""
from __future__ import annotations

import argparse
import sys

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

import env_config

IMAGE_NAME = env_config.DEFAULT_DISCOVERY_ISO_NAME


def get_api() -> AirApi:
    kwargs: dict = {"api_key": env_config.air_api_key()}
    if url := env_config.air_api_url():
        kwargs["api_url"] = url
    api = AirApi.with_api_key(**kwargs)
    if org := env_config.air_ngc_org():
        api.client.headers["Nv-Ngc-Org"] = org
    return api


def _find_image(api: AirApi, name: str):
    return next(
        (img for img in api.images.list(search=name) if img.name == name),
        None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        help="Air image name (default: dsxair-discovery-iso from env_config). "
        "Use a new name to bust Air CDROM cache after infraenv changes.",
    )
    args = parser.parse_args()

    image_name = args.name or IMAGE_NAME
    iso_path = env_config.discovery_iso_path(must_exist=True)
    api = get_api()
    existing = _find_image(api, image_name)

    if existing is not None:
        print(
            f"Air image {image_name!r} already exists (id={existing.id}, "
            f"upload_status={existing.upload_status!r}). Skipping upload. "
            "Pass --name <new-name> to upload fresh content under a new image "
            "(Air won't let you clear/overwrite content on an image already "
            "attached to nodes, and doing so unattached can still serve a "
            "stale CDROM cache)."
        )
        return

    print(f"Uploading {iso_path} as Air image {image_name!r} ...")
    image = api.images.create(
        name=image_name,
        version="2.0.0",
        default_username="core",
        default_password="password",  # cosmetic; API rejects blank
        cpu_arch="x86",
        filepath=str(iso_path),
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")
    print(
        "Now ensure blank-100g exists (upload_blank_disk.py), then run "
        "01_create_simulation.py."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

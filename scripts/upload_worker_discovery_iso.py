#!/usr/bin/env python3
"""
Upload the worker node's discovery ISO to Air as an image named
`worker-discovery-iso` — must exist in Air *before* `topology.json` is
imported, since `sno-worker-1`'s `"cdrom"` field references it by name (see
../README.md, "Adding a worker node?").

Don't have the real per-worker discovery ISO yet? Run this script as-is
first: it uploads PLACEHOLDER_ISO_PATH (defaults to the same
`dsxair-discovery-iso` file already used for `sno-cluster`) under the name
`worker-discovery-iso`. This isn't a dummy/fake file — Assisted Installer's
discovery ISO doesn't encode a role, so this is a fully working discovery
ISO from the start (see the README note on this). It just means
`sno-worker-1` phones home using the same ISO content as `sno-cluster`
until you swap in a dedicated one.

Got the real worker-specific ISO later and want to swap it in? Use
`--replace`:

    python upload_worker_discovery_iso.py --replace /path/to/real-worker-discovery.iso

This uses the Air SDK's `image.clear_upload()` + `image.upload()` pair (see
https://docs.nvidia.com/air/sdk/latest/examples/images.html — "Reset/clear
the file content associated with an Image") to swap the file content
IN PLACE, under the exact same image id/name. Nothing else has to change:
`topology.json`'s `"cdrom": "worker-discovery-iso"` reference stays valid,
and if `sno-worker-1` already exists and has this image attached as its
live cdrom, that attachment is by image id — it automatically picks up the
new content without any node patch or simulation restart. (You will still
want to actually re-trigger discovery on the node afterward — see
02_attach_discovery_iso.py / the README's rebuild note — since it already
booted once from the old content.)

Install deps:
    pip install nv-air-sdk

Run:
    python upload_worker_discovery_iso.py
    python upload_worker_discovery_iso.py --replace /path/to/real-worker-discovery.iso
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from air_sdk import AirApi
from air_sdk.utils import wait_for_state

# --- Fill these in -----------------------------------------------------

# Do not hardcode a real key here — this file lives in the repo. Set
# AIR_API_KEY in your shell environment, or drop it in API_KEY_FILE below —
# either works, no need to `export` it manually every session.
API_KEY = None  # e.g. "nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                # leave None to fall back to AIR_API_KEY / API_KEY_FILE below

API_KEY_FILE = "~/.air_api_key.txt"  # used if API_KEY is None and AIR_API_KEY isn't set

# Placeholder content used the first time this script runs (before you have
# a dedicated worker ISO). Reuses the same file as upload_discovery_iso.py.
PLACEHOLDER_ISO_PATH = "/home/sdambo/Downloads/dsxair-discovery.iso"

IMAGE_NAME = "worker-discovery-iso"  # must match sno-worker-1's "cdrom" in ../topology.json

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


def _find_image(api: AirApi):
    return next((img for img in api.images.list(search=IMAGE_NAME) if img.name == IMAGE_NAME), None)


def create_placeholder(api: AirApi) -> None:
    existing = _find_image(api)
    if existing is not None:
        raise SystemExit(
            f"Image {IMAGE_NAME!r} already exists (id={existing.id}, "
            f"upload_status={existing.upload_status!r}). Use --replace "
            f"/path/to/iso to swap its content instead of creating a new one."
        )

    print(f"Uploading {PLACEHOLDER_ISO_PATH} as Air image {IMAGE_NAME!r} ...")
    image = api.images.create(
        name=IMAGE_NAME,
        version="1.0.0",
        default_username="core",
        default_password="password",  # cosmetic; API rejects blank
        cpu_arch="x86",
        filepath=PLACEHOLDER_ISO_PATH,
    )
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Upload complete: image id={image.id}, name={image.name!r}")
    print(
        f"Now import ../topology.json (its sno-worker-1 \"cdrom\" field "
        f"already references '{IMAGE_NAME}') and continue with Step 3.\n"
        f"When you have a real, dedicated worker discovery ISO later, swap "
        f"it in with:\n"
        f"    python {Path(__file__).name} --replace /path/to/real-worker-discovery.iso"
    )


def replace_content(api: AirApi, new_iso_path: str) -> None:
    image = _find_image(api)
    if image is None:
        raise SystemExit(
            f"Image {IMAGE_NAME!r} doesn't exist yet — run this script with "
            f"no arguments first to create the placeholder."
        )

    print(f"Current image {IMAGE_NAME!r} (id={image.id}): "
          f"upload_status={image.upload_status!r} hash={image.hash!r}")

    print("Clearing existing upload content ...")
    image.clear_upload()
    image.refresh()
    print(f"  upload_status now: {image.upload_status!r} hash: {image.hash!r}")

    print(f"Uploading {new_iso_path} ...")
    image.upload(filepath=new_iso_path)
    wait_for_state(image, "COMPLETE", state_field="upload_status", error_states="READY")
    print(f"Swap complete: upload_status={image.upload_status!r} hash={image.hash!r}")
    print(
        "Same image id/name as before, so topology.json and any node "
        "already using this cdrom don't need any changes. If sno-worker-1 "
        "already booted once from the old content, re-trigger discovery "
        "(see 02_attach_discovery_iso.py or the README's rebuild note) to "
        "make it boot the new ISO."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        metavar="ISO_PATH",
        help="Swap in new content for the existing worker-discovery-iso image "
        "(clear_upload + upload), instead of creating it fresh.",
    )
    args = parser.parse_args()

    api = get_api()

    if args.replace:
        replace_content(api, args.replace)
    else:
        create_placeholder(api)


if __name__ == "__main__":
    main()

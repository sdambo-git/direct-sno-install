#!/usr/bin/env python3
"""
Create and upload a sparse 300G blank disk for SNO (Air image `blank-300g`).

This image is the disk backing that VM. Keep `blank-100g` for HA labs.

    uv run upload_blank_disk_sno.py
    uv run upload_blank_disk_sno.py --replace

Then set spec `control_plane.disk_gb: 300` so topology `"os"` is `blank-300g`.
"""
from __future__ import annotations

import argparse

import env_config
from upload_blank_disk import run_upload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the file content of an existing Air image with the same name.",
    )
    args = parser.parse_args()
    run_upload(
        name=env_config.DEFAULT_SNO_BLANK_IMAGE_NAME,
        size=env_config.DEFAULT_SNO_BLANK_DISK_SIZE,
        replace=args.replace,
    )


if __name__ == "__main__":
    main()

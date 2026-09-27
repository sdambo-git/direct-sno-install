#!/usr/bin/env python3
"""Attach the IBI configuration ISO (wrapper for ``dsx-air ibi attach-config``)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsx_air.ibi import (
    DEFAULT_CONFIG_IMAGE_NAME,
    DEFAULT_NODE,
    attach_cdrom,
    config_iso_path,
)

DEFAULT_SIM = "dsx-ibi-target"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sim",
        default=os.environ.get("SIMULATION_NAME", "").strip() or DEFAULT_SIM,
    )
    parser.add_argument("--node", default=DEFAULT_NODE)
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--name", default=DEFAULT_CONFIG_IMAGE_NAME)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()
    attach_cdrom(
        sim_name=args.sim,
        node_name=args.node,
        image_name=args.name,
        iso=None if args.skip_upload else (args.iso or config_iso_path()),
        replace=args.replace,
        skip_upload=args.skip_upload,
    )
    print(
        f"\n{args.node} boots hd first with {args.name!r} in the CD-ROM.\n"
        "  lsblk -o NAME,SIZE,LABEL   # sr0 should be cluster-config\n"
        "When 6443 is up, use ibi-config-iso-workdir/auth/kubeconfig."
    )


if __name__ == "__main__":
    main()

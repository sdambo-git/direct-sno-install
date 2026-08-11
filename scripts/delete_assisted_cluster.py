#!/usr/bin/env python3
"""
Delete the Assisted Installer SaaS cluster (and its infraenv) created for
this lab. Companion to 00_create_discovery_iso.py --force.

    uv run delete_assisted_cluster.py
"""
from __future__ import annotations

import argparse
import sys

from ailib import AssistedClient

import env_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag before deleting.",
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to delete without --yes.")

    name = env_config.cluster_name()
    infraenv = f"{name}_infra-env"
    ai = AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=True,
    )

    if any(e.get("name") == infraenv for e in ai.list_infra_envs()):
        print(f"Deleting infraenv {infraenv!r} ...")
        ai.delete_infra_env(infraenv, force=True)
    else:
        print(f"No infraenv named {infraenv!r}.")

    if any(c.get("name") == name for c in ai.list_clusters()):
        print(f"Deleting cluster {name!r} ...")
        ai.delete_cluster(name)
    else:
        print(f"No cluster named {name!r}.")

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

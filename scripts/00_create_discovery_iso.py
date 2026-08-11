#!/usr/bin/env python3
"""
Create an Assisted Installer SaaS SNO cluster + infraenv and download the
discovery ISO locally.

Uses ailib (from the aicli package) against https://api.openshift.com.
Requires AI_OFFLINETOKEN (or AI_OFFLINETOKEN_FILE), PULL_SECRET_PATH,
OCP_VERSION, and an SSH public key (SSH_PUBLIC_KEY_PATH or ~/.ssh default).

Run from scripts/:
    uv run 00_create_discovery_iso.py
    uv run 00_create_discovery_iso.py --force   # delete and recreate
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from ailib import AssistedClient

import env_config


def _get_client() -> AssistedClient:
    return AssistedClient(
        url=env_config.SAAS_AI_URL,
        offlinetoken=env_config.ai_offlinetoken(),
        quiet=True,
    )


def _cluster_exists(ai: AssistedClient, name: str) -> bool:
    return any(c.get("name") == name for c in ai.list_clusters())


def _infraenv_exists(ai: AssistedClient, name: str) -> bool:
    return any(e.get("name") == name for e in ai.list_infra_envs())


def _cluster_overrides() -> dict:
    return {
        "openshift_version": env_config.ocp_version(),
        "cpu_architecture": "x86_64",
        "pull_secret": str(env_config.pull_secret_path()),
        "ssh_public_key": env_config.ssh_public_key(),
        "base_dns_domain": env_config.base_dns_domain(),
        "sno": True,
        "infraenv": "false",
    }


def _infraenv_overrides(cluster: str) -> dict:
    return {
        "cluster": cluster,
        "openshift_version": env_config.ocp_version(),
        "cpu_architecture": "x86_64",
        "pull_secret": str(env_config.pull_secret_path()),
        "ssh_public_key": env_config.ssh_public_key(),
        "image_type": "minimal-iso",
    }


def _ensure_cluster(ai: AssistedClient, name: str, *, force: bool) -> None:
    exists = _cluster_exists(ai, name)
    if exists and not force:
        print(f"Reusing existing Assisted Installer cluster {name!r}.")
        return
    if exists and force:
        print(f"Deleting existing cluster {name!r} (--force) ...")
    print(f"Creating Assisted Installer SNO cluster {name!r} ...")
    ai.create_cluster(name, _cluster_overrides(), force=force)


def _ensure_infraenv(ai: AssistedClient, cluster: str, infraenv: str, *, force: bool) -> None:
    exists = _infraenv_exists(ai, infraenv)
    if exists and force:
        print(f"Deleting existing infraenv {infraenv!r} (--force) ...")
        ai.delete_infra_env(infraenv, force=True)
        exists = False
    if exists:
        print(f"Reusing existing infraenv {infraenv!r}.")
        return
    print(f"Creating infraenv {infraenv!r} ...")
    ai.create_infra_env(infraenv, _infraenv_overrides(cluster))


def _download_iso(ai: AssistedClient, infraenv: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    download_dir = dest.parent
    print(f"Downloading discovery ISO for {infraenv!r} into {download_dir} ...")
    ai.download_iso(infraenv, str(download_dir))
    downloaded = download_dir / f"{infraenv}.iso"
    if not downloaded.is_file():
        raise SystemExit(f"Expected ISO missing after download: {downloaded}")
    if downloaded.resolve() != dest.resolve():
        shutil.move(str(downloaded), str(dest))
    print(f"Discovery ISO ready at {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and recreate the cluster/infraenv if they already exist.",
    )
    args = parser.parse_args()

    name = env_config.cluster_name()
    infraenv = f"{name}_infra-env"
    dest = env_config.discovery_iso_path()

    ai = _get_client()
    _ensure_cluster(ai, name, force=args.force)
    _ensure_infraenv(ai, name, infraenv, force=args.force)
    _download_iso(ai, infraenv, dest)

    print(
        "\nNext: upload the ISO to Air as "
        f"{env_config.DEFAULT_DISCOVERY_ISO_NAME!r}:\n"
        f"  DISCOVERY_ISO_PATH={dest} uv run upload_discovery_iso.py\n"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface ailib/sys.exit noise cleanly
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

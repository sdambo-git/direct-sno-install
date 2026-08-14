# Scripts reference

This directory holds every script used by the DSX-Air OpenShift install flows
documented in `../README.md`. That README is the source of truth for the
end-to-end walkthrough; this file indexes the scripts.

Run them from inside this `scripts/` directory (`cd scripts && uv run
01_create_simulation.py`, etc.) since several import each other by module
name.

Auth / inputs are resolved by `env_config.py` — see the README table for
`AIR_API_KEY`, `AI_OFFLINETOKEN`, `PULL_SECRET_PATH`, `OCP_VERSION`,
`CLUSTER_PROFILE`, and related variables.

## Normal install flow — SNO (in order)

| # | Script | Run when | What it does |
|---|---|---|---|
| 0 | `00_create_discovery_iso.py` | Once (or after `--force`) | Creates Assisted Installer SaaS SNO cluster + infraenv via `ailib`, downloads the discovery ISO locally. Idempotent reuse; `--force` recreates. |
| — | `upload_discovery_iso.py` | After step 0 | Uploads the local ISO to Air (name must match topology `cdrom`). Skips if present unless `--replace`. |
| — | `upload_blank_disk.py` | Before first import | Creates sparse local 100G qcow2 (if needed) and uploads Air image `blank-100g`. Skips if present unless `--replace`. |
| 1 | `01_create_simulation.py` | After both Air images exist | Imports topology manifest (`topology.json` by default) and starts the simulation. Sets up jump-host SSH and clears the first-login password. |
| 6 | `06_wait_for_host_ipv4.py` | After the node boots discovery | Polls Assisted Installer until host(s) show OOB IPv4 `192.168.200.x`. Does not start install. |
| 7 | `07_install_cluster.py` | After wait succeeds | Configures networking/VIPs, starts install, downloads kubeconfig. |
| 9 | `09_recover_to_discovery.py` | Failed install / no bootable device | `node.rebuild()` + re-attach discovery ISO; optional `--reset-ai`. |

## Normal install flow — multinode (3-node HA)

Set `CLUSTER_PROFILE=multinode` (or pass `--profile multinode` to step 0).
Uses `topology-multinode.json` and `CLUSTER_NAME=ocp-cluster` by default.

| # | Script | Notes |
|---|---|---|
| 0 | `00_create_discovery_iso.py --profile multinode` | Creates non-SNO AI cluster (`sno: false`, `control_plane_count=3`). |
| — | `delete_assisted_cluster.py --yes` | Run before `00 --force` when recreating AI objects. |
| — | `upload_discovery_iso.py --name <topology cdrom>` | Use a **new** ISO name after each `00 --force`. |
| — | `upload_blank_disk.py` | Same as SNO. |
| — | `verify_topology_alignment.py` | Checks topology `cdrom` + `blank-100g` exist in Air. |
| 1 | `01_create_simulation.py` | Imports `topology-multinode.json` (`ocp-cp-0` … `ocp-cp-2`). |
| 6 | `06_wait_for_host_ipv4.py --require-known --min-hosts 3` | Waits for all three hosts. |
| — | `assign_host_roles.py` | Optional; assigns `master` to all CP topology nodes. |
| 7 | `07_install_cluster.py` | Sets API/Ingress VIPs (`API_VIP`, `INGRESS_VIP`), installs, downloads credentials. |
| 9 | `09_recover_to_discovery.py --node <name>` | Per-node recovery. |

## Assisted Installer helpers

| Script | What it does |
|---|---|
| `delete_assisted_cluster.py` | Deletes the SaaS cluster + infraenv (`--yes` required). Companion to `00_create_discovery_iso.py --force`. |
| `assign_host_roles.py` | Maps topology node names to AI `master`/`worker` roles (multinode). |

## Recovery / re-run helpers

| Script | Run when | What it does |
|---|---|---|
| `02_attach_discovery_iso.py` | Redo discovery after Abort/Reset in the console | Re-attaches discovery ISO. Older pattern — prefer `09_recover_to_discovery.py`. |
| `03_boot_to_disk.py` | Legacy | Discouraged; see README blank-disk pattern. |
| `04_create_jump_host_service.py` | Any time after `01` | Idempotent SSH Service on `oob-mgmt-server` + first-login password bootstrap; prints `ssh` command. |
| `bootstrap_jump_host.py` | Jump host SSH fails with expired password | Re-run password bootstrap only (also creates SSH service if missing). |
| `09_recover_to_discovery.py` | Failed install / no bootable device | Rebuild blank disk + re-attach ISO; `--node` for multinode; `--reset-ai` for AI cluster reset. |

**Caveat on `02`/`03`:** README's blank-disk + permanent `["hd","cdrom"]` note explains why `node.rebuild()` is preferred over toggling boot order.

## Standalone / alternative-path scripts

| Script | What it's for |
|---|---|
| `upload_qcow2_image.py` | Upload an arbitrary pre-installed qcow2 as an Air VM image (`QCOW2_PATH`, optional `IMAGE_NAME`). Different from the discovery-ISO flow. |
| `host-creation.py` | Add an ad hoc utility node to a running simulation (brief stop/start required). |
| `import_topology.py` | Older import helper superseded by `01_create_simulation.py`. |

## Read-only diagnostic scripts

| Script | What it's for |
|---|---|
| `verify_topology_alignment.py` | Checks that topology `cdrom` / `os` image names exist in Air before import. |
| `diagnose_import.py` | Dumps raw API response for the active simulation. |

## Shared helper modules

| File | What it's for |
|---|---|
| `env_config.py` | Env / `*_FILE` / path resolution; `CLUSTER_PROFILE`, topology path, VIPs, node names. |
| `air_common.py` | Air stop/checkpoint/start dance, simulation/node lookup, jump-host helpers. |
| `assisted_common.py` | Assisted Installer host/cluster helpers, multi-host readiness checks. |

## Non-script YAML files (not part of this automation)

Carried over from an image-based / Lifecycle Agent workflow this README does
not use:

- `seedgenerator.yaml` — `SeedGenerator` CR reference.
- `seedgen.yaml` — **gitignored** quay credential.
- `set-core-user-password-machineconfig.yaml` — **gitignored** password hash MachineConfig.

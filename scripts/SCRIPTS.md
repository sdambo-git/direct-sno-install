# Scripts reference

This directory holds every script used by the SNO-on-DSX-Air install
documented in `../README.md`. That README is the source of truth for the
end-to-end walkthrough; this file indexes the scripts.

Run them from inside this `scripts/` directory (`cd scripts && uv run
01_create_simulation.py`, etc.) since several import each other by module
name.

Auth / inputs are resolved by `env_config.py` — see the README table for
`AIR_API_KEY`, `AI_OFFLINETOKEN`, `PULL_SECRET_PATH`, `OCP_VERSION`, and
related variables.

## Normal install flow (in order)

| # | Script | Run when | What it does |
|---|---|---|---|
| 0 | `00_create_discovery_iso.py` | Once (or after `--force`) | Creates Assisted Installer SaaS SNO cluster + infraenv via `ailib`, downloads the discovery ISO locally. Idempotent reuse; `--force` recreates. |
| — | `upload_discovery_iso.py` | After step 0 | Uploads the local ISO to Air as `dsxair-discovery-iso`. Skips if present unless `--replace`. |
| — | `upload_blank_disk.py` | Before first import | Creates sparse local 100G qcow2 (if needed) and uploads Air image `blank-100g`. Skips if present unless `--replace`. |
| 1 | `01_create_simulation.py` | After both Air images exist | Imports `../topology.json` and starts the simulation (creates `sno-cluster` + implicit OOB mgmt nodes). Sets up jump-host SSH and clears the first-login password. Refuses if `sno-cluster` already exists — delete in Air UI first. |
| 6 | `06_wait_for_host_ipv4.py` | After the node boots discovery | Polls Assisted Installer until a host shows OOB IPv4 `192.168.200.x`. Does not start install. |
| — | *(Assisted Installer console)* | After wait succeeds | Networking VIPs → validations → Install cluster. See README Step 5. |
| 5 | `05_detach_discovery_iso.py` | After install is stable | Optional: detach discovery ISO / `hd`-only boot. |

## Assisted Installer helpers

| Script | What it does |
|---|---|
| `delete_assisted_cluster.py` | Deletes the SaaS cluster + infraenv (`--yes` required). Companion to `00_create_discovery_iso.py --force`. |

## Recovery / re-run helpers

| Script | Run when | What it does |
|---|---|---|
| `02_attach_discovery_iso.py` | Redo discovery after Abort/Reset in the console | Re-attaches `dsxair-discovery-iso` and sets `boot` to cdrom-first. Older pattern — prefer `node.rebuild()` today. |
| `03_boot_to_disk.py` | At "Writing image to disk: 100%" / before reboot | Detaches cdrom / `hd`-only so reboot lands on installed disk. Older pattern. |
| `04_create_jump_host_service.py` | Any time after `01` | Idempotent SSH Service on `oob-mgmt-server` + first-login password bootstrap; prints `ssh` command. |
| `bootstrap_jump_host.py` | Jump host SSH fails with expired password | Re-run password bootstrap only (also creates SSH service if missing). |

**Caveat on `02`/`03`:** README's blank-disk + permanent `["hd","cdrom"]` note explains why `node.rebuild()` is preferred over toggling boot order.

## Standalone / alternative-path scripts

| Script | What it's for |
|---|---|
| `upload_qcow2_image.py` | Upload an arbitrary pre-installed qcow2 as an Air VM image (`QCOW2_PATH`, optional `IMAGE_NAME`). Different from the discovery-ISO flow. |
| `host-creation.py` | Add an ad hoc utility node to a running `sno-cluster` sim (brief stop/start required). |
| `import_topology.py` | Older import helper superseded by `01_create_simulation.py`. |

## Read-only diagnostic scripts

| Script | What it's for |
|---|---|
| `verify_topology_alignment.py` | Checks that `topology.json` `cdrom` / `os` image names exist in Air before import. |
| `diagnose_import.py` | Dumps raw API response for the `sno-cluster` simulation. |

## Shared helper modules

| File | What it's for |
|---|---|
| `env_config.py` | Env / `*_FILE` / path resolution for Air + Assisted Installer inputs. |
| `air_common.py` | Air stop/checkpoint/start dance, simulation/node lookup, jump-host helpers (service + password bootstrap). |

## Non-script YAML files (not part of this automation)

Carried over from an image-based / Lifecycle Agent workflow this README does
not use:

- `seedgenerator.yaml` — `SeedGenerator` CR reference.
- `seedgen.yaml` — **gitignored** quay credential.
- `set-core-user-password-machineconfig.yaml` — **gitignored** password hash MachineConfig.

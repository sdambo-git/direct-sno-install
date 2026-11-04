# OpenShift SNO / OCP on NVIDIA DSX Air

Standard Single Node OpenShift install using an **Assisted Installer**
discovery ISO on NVIDIA DSX / NVIDIA Air — no seed image, no Lifecycle
Agent, no `MachineConfig` partition tricks, no `cdrom`-swap sequence. One
Air node, one boot, one install.

This fork supports **SNO** (`topology.json`), an R&D **multinode profile**
(`topology-multinode.json`: 3-node HA, 3 control-plane masters), and the
**NVIDIA DSX fabric** topology (`Topology-NoDPU.json`) covered below. The
manual SNO walkthrough lives in [sno.md](sno.md); the 3-node HA profile in
[multi-node.md](multi-node.md).

## Contents

- [Prerequisites](#prerequisites)
- [Auth and inputs (env / files)](#auth-and-inputs-env--files)
- [Quick path (happy path)](#quick-path-happy-path)
- [Important: use Air's OOB network](#important-use-airs-oob-network)
- [Important: don't pin `management_mac` in `topology.json`](#important-dont-pin-management_mac-in-topologyjson)
- [Important: boot order stays `["hd", "cdrom"]`](#important-boot-order-stays-hd-cdrom--dont-toggle-it)
- [Important: Air images must exist *before* you import the topology](#important-air-images-must-exist-before-you-import-the-topology)
- [What the orchestrator does, step by step](#what-the-orchestrator-does-step-by-step)
- [Multinode profile (3-node HA)](multi-node.md#multinode-profile-3-node-ha)
- [NVIDIA DSX fabric with an image-based SNO node (`Topology-NoDPU.json`)](#nvidia-dsx-fabric-with-an-image-based-sno-node-topology-nodpujson)
  - [Check it's working with `oc`](#check-its-working-with-oc)
- [Scripts reference](#scripts-reference)
- [About `scripts/air_common.py` and `scripts/env_config.py`](#about-scriptsair_commonpy-and-scriptsenv_configpy)

## Prerequisites

- Red Hat pull secret from
  `console.redhat.com/openshift/install/pull-secret`.
- Red Hat Assisted Installer offline token from
  `cloud.redhat.com/openshift/token` (for the SaaS API / `ailib`).
- An SSH public key to bake into the node.
- NVIDIA Air / DSX Air API access (`nv-air-sdk`). Generate an NGC Personal
  API Key at `org.ngc.nvidia.com/setup/api-keys` with **NVIDIA Air** under
  **Services Included**. Your org's `air` role covers simulation/read image
  access; ISO uploads may also need `air-image-uploader`.
- `qemu-img` on PATH (to create the sparse `blank-100g` disk).
- Python deps via `uv sync` from this repo (`aicli` + `nv-air-sdk`).

SNO sizing in `topology.json`: 16 vCPU / 64 GB RAM / 100 GB disk per node.
Check your org quota in the Air UI before importing a multinode topology.

## Auth and inputs (env / files)

Scripts resolve configuration from the environment (or `*_FILE` paths). Do
not hardcode secrets in the repo.

| Input | How to set |
|---|---|
| Air API key | `AIR_API_KEY` or `AIR_API_KEY_FILE` — NGC **Personal** key (your user) or **Service** key (org automation). Not OpenShift `kubeadmin`. |
| Assisted Installer offline token | `AI_OFFLINETOKEN` or `AI_OFFLINETOKEN_FILE` |
| Pull secret | `PULL_SECRET_PATH` (path to JSON file) |
| SSH public key (OpenShift nodes) | `SSH_PUBLIC_KEY_PATH` (default `~/.ssh/id_ed25519.pub` or `id_rsa.pub`) — baked into the discovery ISO |
| SSH public key (Air jump host) | The key you add in the **Air UI** (account / simulation SSH keys). `ssh` to `oob-mgmt-server` uses that key, not necessarily `id_ed25519`. |
| OpenShift version | `OCP_VERSION` (**required**, e.g. `4.19`) |
| Cluster name | `CLUSTER_NAME` (default `sno-cluster`, or `ocp-cluster` when `CLUSTER_PROFILE=multinode`) |
| Cluster profile | `CLUSTER_PROFILE` (`sno` default, or `multinode`) |
| Topology manifest | `TOPOLOGY_PATH` (default `topology.json` or `topology-multinode.json`) |
| Expected AI hosts | `EXPECTED_HOSTS` (default: node count in topology) |
| API VIP (multinode) | `API_VIP` (default `192.168.200.10`) |
| Ingress VIP (multinode) | `INGRESS_VIP` (default `192.168.200.11`) |

Shared resolution lives in `scripts/env_config.py`, which also documents the
further optional overrides (e.g. `AIR_NGC_ORG`, `DISCOVERY_TIMEOUT`,
`ADDITIONAL_NTP_SOURCE`, `BASE_DNS_DOMAIN`, `JUMP_HOST_PASSWORD`).

## Quick path (happy path)

`scripts/run_cluster.py` wraps the whole install — SNO and multinode alike —
behind one entry point, so you don't have to copy-paste each `uv run ...`
command by hand. Run from `scripts/` (scripts import each other by module
name):

```bash
cd scripts
export AIR_API_KEY_FILE=~/.config/dsx-air/air-api-key
export AI_OFFLINETOKEN_FILE=~/.config/dsx-air/ai-offlinetoken
export PULL_SECRET_PATH=~/.config/dsx-air/pull-secret.json
export OCP_VERSION=4.22

uv run run_cluster.py --all --yes   # everything, accepting every prompt's default
```

Other ways to drive it:

```bash
uv run run_cluster.py              # interactive menu — pick a step, a range, or 'all'
uv run run_cluster.py --list       # print the numbered steps and exit
uv run run_cluster.py --run 3-6    # run a specific range non-interactively
uv run run_cluster.py --recover ocp-cp-1   # ad hoc: rebuild + re-attach discovery ISO
```

It runs the exact scripts documented in
[What the orchestrator does, step by step](#what-the-orchestrator-does-step-by-step)
below (nothing new happening under the hood), tracks per-step pass/fail for
the session so you can retry just the step that failed, and on the
multinode profile it automates the "mint a fresh ISO name and update every
node's `cdrom`" step that's manual in [sno.md](sno.md). Every prompt has a
safe default (shown when you answer, or picked automatically under
`--yes`/non-interactive stdin), so it's CI-friendly too. Full reference:
`scripts/SCRIPTS.md`.

For the 3-node HA profile add `CLUSTER_PROFILE=multinode` — see
[multi-node.md](multi-node.md). For the NVIDIA DSX fabric topology see
[NVIDIA DSX fabric with an image-based SNO node](#nvidia-dsx-fabric-with-an-image-based-sno-node-topology-nodpujson)
below. If you'd rather not use `ailib` at all, the Assisted Installer
console fallback in [sno.md](sno.md) is still valid.

## Important: use Air's OOB network

OpenShift uses Air's **OOB management network**, which hands out routable `192.168.200.x/24`-range addresses (via its own DHCP/DNS/NAT on
`oob-mgmt-server`). 

**First-login password on `oob-mgmt-server`:** the auto-provisioned jump host
ships with user `ubuntu` / a factory password and *requires* a password
change on first SSH. Pubkey login uses the SSH key you added in the **Air UI**
(not `SSH_PUBLIC_KEY_PATH`, which is only for the OpenShift nodes). Until the
password is changed, pubkey auth connects but every command fails with
`Password change required but no TTY available`.
`01_create_simulation.py` and `04_create_jump_host_service.py` now run the
bootstrap automatically (via `expect`). Override the new password with
`JUMP_HOST_PASSWORD` (default `redhat`). Re-run manually with
`uv run bootstrap_jump_host.py` if needed.

## Important: don't pin `management_mac` in `topology.json`

Don't set the field at all — delete it entirely and let Air assign whatever
MAC it wants.

```json
{
    "format": "JSON",
    "ztp": null,
    "content": {
        "nodes": {
            "sno-cluster": {
                "cpu": 16,
                "memory": 65536,
                "storage": 100,
                "nic_model": "virtio",
                "cpu_mode": "host-passthrough",
                "cpu_options": [],
                "secureboot": false,
                "os": "blank-100g",
                "storage_pci": null,
                "pxehost": false,
                "cdrom": "dsxair-discovery-iso",
                "boot": ["hd", "cdrom"],
                "features": {
                    "uefi": false
                }
            }
        },
        "links": []
    },
    "name": "sno-cluster"
}
```

If you need to know the node's assigned MAC/IP after the fact (e.g. to
debug DHCP yourself), read it back from the live node/interface via the API
after the simulation is `ACTIVE` — don't try to pre-declare it in the
manifest.

**Result:** `sno-cluster` got its real `192.168.200.2` OOB lease on the first try,
instead of falling back to a link-local address.

## Important: boot order stays `["hd", "cdrom"]` — don't toggle it

`topology.json` sets `"boot": ["hd", "cdrom"]` and an `"os": "blank-100g"`
disk (a genuinely empty, unbootable qcow2 image — see
`scripts/upload_blank_disk.py`) rather than a real OS image. This
combination is intentional and is meant to be **left alone** through the
entire install lifecycle:

- **First boot:** firmware tries `hd` first, finds nothing bootable (the
  disk is genuinely blank), and falls through to `cdrom` automatically —
  no manual boot-order flip needed to get into the discovery ISO.
- **Post-install reboot:** once Assisted Installer writes the OS to disk,
  `hd` is bootable, so it wins immediately (it's still first in the list)
  — again with zero manual changes.
- **Re-running discovery later** (e.g. after a failed install where you
  want a totally clean re-attempt): don't touch the `boot` field. Instead,
  rebuild the node (`node.rebuild()`), which resets its disk back to the
  blank `blank-100g` template. That makes `hd` unbootable again, so the
  very next boot naturally falls through to `cdrom` again — same
  `["hd", "cdrom"]` order the whole time.

## Important: Air images must exist *before* you import the topology

Importing a topology that references missing `cdrom` / `os` image names
fails validation and leaves the simulation `INVALID`. Both of these must
already exist in your org at **import time**:

- `dsxair-discovery-iso` (discovery ISO uploaded in [sno.md](sno.md) Steps 1–2)
- `blank-100g` (sparse blank qcow2 from `upload_blank_disk.py`)

`topology.json` uses Air's real schema shape (`nic_model` / `cpu_mode` /
`secureboot`, `"features": {"uefi": ...}`) rather than older public-doc
placeholders.

## What the orchestrator does, step by step

The 11 steps `run_cluster.py` runs (`--list` prints the same list):

| # | Step | Script |
|---|------|--------|
| 1 | Delete existing Assisted Installer cluster (optional, destructive) | `delete_assisted_cluster.py` |
| 2 | Create Assisted Installer cluster + download discovery ISO | `00_create_discovery_iso.py` |
| 3 | Upload discovery ISO to Air (fresh name for multinode + topology update) | `upload_discovery_iso.py` |
| 4 | Upload blank-100g disk image to Air | `upload_blank_disk.py` |
| 5 | Verify topology image alignment (read-only) | `verify_topology_alignment.py` |
| 6 | Import topology + start Air simulation | `01_create_simulation.py` |
| 7 | Wait for host discovery (OOB IPv4 from Assisted Installer) | `06_wait_for_host_ipv4.py` |
| 8 | Assign host roles (master/worker, multinode only) | `assign_host_roles.py` |
| 9 | Configure cluster networking (gate, no install) | `07_install_cluster.py --configure-only` |
| 10 | Install the OpenShift cluster | `07_install_cluster.py` |
| 11 | Install oc, kubeconfig, and cluster DNS on the jump host | `08_verify_cluster.py` |

The manual SNO walkthrough in [sno.md](sno.md) runs the same flow by hand;
the multinode and NoDPU sections have their own step-by-step variants. Use
a manual flow when a specific step fails and you want to re-run or debug
just that piece, or when you'd rather drive the Assisted Installer UI
yourself.

## Multinode profile (3-node HA)

The R&D 3-node HA profile (`topology-multinode.json`) — resource budget,
quick path, manual steps, and phased validation — now lives in
[multi-node.md](multi-node.md).

## NVIDIA DSX fabric with an image-based SNO node (`Topology-NoDPU.json`)

`Topology-NoDPU.json` (repo root) is a hand-written Air manifest modeling an
NVIDIA DSX reference fabric: 4 Cumulus VX switches (`ew-leaf-*`,
`ew-spine-*`), 3 OOB-side switches (`ns-leaf-*`, `ns-spine-*`,
`ns-oob-leaf-01`), and 8 compute nodes. One of them, `ocp-cp-0` (renamed
from `hgx-su01-n01`), is configured as an **image-based SNO (IBI)** target —
`os: blank-300g`, `cdrom: rhcos-ibi-4.22.14`, `boot: [hd, cdrom]`,
16 vCPU / 64 GB / 300 GB — instead of an Assisted Installer discovery-ISO
node. The rest (`hgx-su01-n02`..`n08`) are plain Ubuntu nodes, unrelated to
the OpenShift cluster. Every node pins its own `management_interfaces`
IP/MAC on `192.168.200.0/24`, so OOB addressing is static, not Air DHCP
order; `ocp-cp-0` sits at `192.168.200.11`.

**This file used to run 3 control planes via Assisted Installer**
(`ocp-cp-0/1/2`, all discovery-ISO nodes, `EXPECTED_HOSTS=3`,
`API_VIP`/`INGRESS_VIP`). That variant is gone: `ocp-cp-1` and `ocp-cp-2`
were reverted to their original names and plain-Ubuntu spec
(`hgx-su01-n02`/`hgx-su01-n03`), and `ocp-cp-0` now does a single-node
image-based install instead of joining a 3-way Assisted cluster. If you
need the old 3-CP Assisted-HA shape, use the plain `topology-multinode.json`
profile in [multi-node.md](multi-node.md) instead (same 3-CP flow, without
the fabric switches) or check out this file as it stood before this change.

**Shared org quota** for the whole fabric (17 nodes: `ocp-cp-0` at
16 vCPU/64 GB plus 7 small Ubuntu/switch-class nodes) is ~48 vCPU / ~96 GiB —
check your org's resource budget (`api.organizations.list()[0].usage` vs
`.cpu`/`.memory`) before importing.

**This branch's tooling cannot bring this topology up.** `run_cluster.py`
and the numbered `scripts/00`–`08` here are Assisted-Installer-only — no
seed image, no Lifecycle Agent, no image-based-config ISO generation. The
image-based install path (seed generation, live-ISO build,
`ibi config-image`/`apply-config`) only exists as the `dsx-air` CLI package
(`scripts/dsx_air/`, `examples/*.yaml`, `ibi/*.yaml`) on the **`dsx-air-cli`**
branch, which documents this exact topology shape end-to-end under
"NVIDIA DSX fabric with an IBI node" (its `Topology-NoDPU.json` is now the
same shape as this one: one IBI `ocp-cp-0` + plain Ubuntu hosts).

To bring this file up:

1. Get `scripts/dsx_air/`, `examples/ibi-target.yaml`, and `ibi/` from the
   `dsx-air-cli` branch — this branch doesn't carry them.
   `scripts/import_topology.py` here is unchanged from that branch and
   works as-is for importing `Topology-NoDPU.json`; it has no `dsx_air`
   dependency.
2. Follow `dsx-air-cli`'s `README.md` → "NVIDIA DSX fabric with an IBI
   node" section for the rest: wait for IBI prep to finish, reboot once,
   build and apply the image-based config, then verify.

### Check it's working with `oc`

Same verification as `dsx-air-cli`'s single-IBI-node setup, since the node
identity and IP didn't change: expect `ocp-cp-0` `Ready` with
`control-plane,master,worker` at `192.168.200.11`, and cluster operators
`AVAILABLE=True`. See that branch's README "Use the cluster and the web
console" step for the exact `oc`/tunnel/console commands.

## Scripts reference

See `scripts/SCRIPTS.md` for a full index of every script in `scripts/`.

## About `scripts/air_common.py` and `scripts/env_config.py`

`env_config.py` centralizes env / `*_FILE` / path resolution for Air and
Assisted Installer credentials.

`air_common.py` wraps `air_sdk` quirks shared by the numbered Air scripts:

- **Node edits require `INACTIVE`.** Patching `cdrom` / boot (or creating a
  node) while `ACTIVE` is rejected or ignored.
- **Checkpoints block further changes.** Air auto-creates a checkpoint on
  shutdown; it must reach `COMPLETE` before further edits.

| Function | Purpose | Used by |
|---|---|---|
| `get_api()` (from `upload_discovery_iso.py`) | Air auth via `AIR_API_KEY` / `AIR_API_KEY_FILE` | Air scripts |
| `get_simulation()` | Look up the `sno-cluster` simulation by name | `02`, `03`, `04`, `host-creation.py` |
| `get_node()` | Look up a node by name within a simulation | `02`, `03` |
| `wait_for_sim_state()` | Poll until `ACTIVE` / `INACTIVE` | `01` and stop/start helpers |
| `stop_simulation_and_clear_checkpoints()` | Stop + clear checkpoints before node edits | `02`, `03`, `host-creation.py` |
| `start_simulation()` | Restart and wait for `ACTIVE` | `02`, `03`, `host-creation.py` |
| `ensure_jump_host_service()` | Idempotent SSH Service on `oob-mgmt-server` | `air_common`, `bootstrap_jump_host.py` |
| `bootstrap_jump_host_password()` | Clear mandatory first-login password change | `ensure_jump_host_ready()` |
| `ensure_jump_host_ready()` | SSH service + password bootstrap | `01`, `04`, `bootstrap_jump_host.py` |
| `jump_host_ssh_command()` | Format the ready-to-use `ssh` command | `01`, `04`, `bootstrap_jump_host.py` |

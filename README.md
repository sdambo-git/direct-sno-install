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
- [NVIDIA DSX fabric multinode HA (`Topology-NoDPU.json`)](#nvidia-dsx-fabric-multinode-ha-topology-nodpujson)
  - [Bring it up from scratch](#bring-it-up-from-scratch)
  - [If host discovery times out with "no hosts yet"](#if-host-discovery-times-out-with-no-hosts-yet)
  - [Check it's working with `oc`](#check-its-working-with-oc)
- [Scripts reference](#scripts-reference)
- [About `scripts/air_common.py` and `scripts/env_config.py`](#about-scriptsair_commonpy-and-scriptsenv_configpy)

## Prerequisites
- Create redhat account by redhat console link , click on "Register for Red Hat account"
  [console.redhat.com](https://console.redhat.com) 
- Red Hat pull secret from redhat console
  [console.redhat.com/openshift/install/pull-secret](https://console.redhat.com/openshift/install/pull-secret)
- Red Hat Assisted Installer offline token from
  [console.redhat.com/openshift/token](https://console.redhat.com/openshift/token) (for the SaaS API / `ailib`).
  click the following `use API tokens to authenticate`
- An SSH public key to bake into the node.
- NVIDIA Air / DSX Air API access (`nv-air-sdk`). Generate an NGC Personal
  API Key at [org.ngc.nvidia.com/setup/api-keys](https://org.ngc.nvidia.com/setup/api-keys) with **NVIDIA Air** under
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
[NVIDIA DSX fabric multinode HA](#nvidia-dsx-fabric-multinode-ha-topology-nodpujson)
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

## NVIDIA DSX fabric multinode HA (`Topology-NoDPU.json`)

`Topology-NoDPU.json` (repo root) is a hand-written Air manifest modeling an
NVIDIA DSX reference fabric: 4 Cumulus VX switches (`ew-leaf-*`,
`ew-spine-*`), 3 OOB-side switches (`ns-leaf-*`, `ns-spine-*`,
`ns-oob-leaf-01`), and 8 compute nodes. Three of them —
`hgx-su01-n01/02/03` — are renamed `ocp-cp-0/1/2` and resized to the same
HA control-plane spec as `topology-multinode.json` (16 vCPU / 64 GB / 100 GB,
`os: blank-100g`, discovery-ISO `cdrom`, `boot: [hd, cdrom]`); the rest
(`hgx-su01-n04`..`n08`) stay plain Ubuntu nodes, unrelated to the OpenShift
cluster. Every node pins its own `management_interfaces` IP/MAC on
`192.168.200.0/24`, so OOB addressing is static, not Air DHCP order.

This whole sequence (import → discovery → role assignment → install →
verify) has been run end-to-end against this file and confirmed working.

**Two things don't match the plain `topology-multinode.json` defaults:**

1. **VIP addresses.** Use `API_VIP=192.168.200.19` and
   `INGRESS_VIP=192.168.200.20`.
2. **Role assignment needed a code fix.** `assign_topology_host_roles()`
   used to require *every* node in the topology manifest to match an
   Assisted Installer host — fine for `topology-multinode.json` (3 nodes,
   all OCP), but `Topology-NoDPU.json` has 17 nodes and only 3 ever register.
   `env_config.expected_topology_node_names()` now bounds the expected
   roster to control-plane names plus only as many worker names as
   `EXPECTED_HOSTS` calls for, so the other 14 fabric/compute nodes are
   correctly ignored. This requires `EXPECTED_HOSTS` to be set accurately
   (`3` here) — if left unset it falls back to counting every manifest node
   again.

**Shared org quota**: the 3×16vCPU/64GB control planes plus the rest of the
fabric need ~85 vCPU / ~230 GiB. Check your org's resource budget
(`api.organizations.list()[0].usage` vs `.cpu`/`.memory`) before importing —
tear down other **ACTIVE** simulations first if there isn't enough headroom,
the same as the plain multinode profile's quota warning in
[multi-node.md](multi-node.md), just with a
bigger ask.

### Bring it up from scratch

If the simulation imports but won't actually boot (stays `INACTIVE` after
`REQUESTING`, or `sim.sleep` stays `True` even after `sim.start()`), that's
the org quota being silently exceeded, not a crash — check
`api.organizations.list()[0].usage` against `.cpu`/`.memory` and free up
another active simulation first.

#### Option A — `run_cluster.py` orchestrator (recommended)

```bash
cd scripts
export CLUSTER_PROFILE=multinode
export CLUSTER_NAME=ocp-nodpu
export OCP_VERSION=4.22
export EXPECTED_HOSTS=3
export TOPOLOGY_PATH=Topology-NoDPU.json
export API_VIP=192.168.200.19
export INGRESS_VIP=192.168.200.20
export AIR_API_KEY_FILE=~/.config/dsx-air/air-api-key
export AI_OFFLINETOKEN_FILE=~/.config/dsx-air/ai-offlinetoken
export PULL_SECRET_PATH=~/.config/dsx-air/pull-secret.json

uv run run_cluster.py --all --yes
```

This runs the same 11 steps as the plain multinode profile in
[multi-node.md](multi-node.md) (see `--list`):
creates the Assisted Installer cluster, builds and uploads a fresh discovery
ISO, rewrites `cdrom` only on nodes that already have that key (so the
fabric/switch nodes are left untouched), imports the Air simulation, waits
for all 3 `ocp-cp-*` hosts to register, assigns `master` to each, configures
VIPs, and installs. Host discovery can take a few minutes to pick up NTP
(`Host couldn't synchronize with any NTP server` warnings are expected and
self-resolve); the install itself takes roughly 40–60 minutes.

#### Option B — manual, step by step

```bash
cd scripts
export CLUSTER_PROFILE=multinode
export CLUSTER_NAME=ocp-nodpu
export OCP_VERSION=4.22
export EXPECTED_HOSTS=3
export TOPOLOGY_PATH=Topology-NoDPU.json
export API_VIP=192.168.200.19
export INGRESS_VIP=192.168.200.20
export AIR_API_KEY_FILE=~/.config/dsx-air/air-api-key
export AI_OFFLINETOKEN_FILE=~/.config/dsx-air/ai-offlinetoken
export PULL_SECRET_PATH=~/.config/dsx-air/pull-secret.json

uv run delete_assisted_cluster.py --yes
uv run 00_create_discovery_iso.py --force --profile multinode

ISO_NAME="dsxair-discovery-$(date +%s)"
uv run upload_discovery_iso.py --name "$ISO_NAME"
# Set cdrom to $ISO_NAME on the 3 ocp-cp-* nodes in Topology-NoDPU.json only
# — they're the only nodes with an existing cdrom key; leave the switches
# and other Ubuntu nodes untouched.
uv run upload_blank_disk.py
uv run verify_topology_alignment.py   # optional sanity check
uv run 01_create_simulation.py
uv run 06_wait_for_host_ipv4.py --min-hosts 3 --require-known
uv run assign_host_roles.py          # optional; 07 also assigns roles
uv run 07_install_cluster.py --configure-only   # gate before install
uv run 07_install_cluster.py
uv run 08_verify_cluster.py
```

Or run the same command without the `--yes` option:

```bash
uv run run_cluster.py --all
```

Same steps and gotchas as Option A, just run one at a time instead of via the
orchestrator — useful for understanding each stage or stopping to inspect
state between them. `08_verify_cluster.py` prints the exact tunnel command
with the real jump-host port/FQDN filled in; use it in the verification
section below.

### If host discovery times out with "no hosts yet"

```
Timed out after 900s waiting for 3 host(s) with OOB IPv4 (last seen: no hosts yet).
Step 7 FAILED: 06_wait_for_host_ipv4.py exited with status 1
```

`06_wait_for_host_ipv4.py`'s default timeout (`env_config.discovery_timeout_seconds()`)
now scales with the **total** node count in the topology, not just the
expected host count — `max(900, 100 * total_nodes)` seconds, so
`Topology-NoDPU.json`'s 17 nodes get ~28 minutes instead of the flat 15. This
is because boot contention comes from Air starting *every* VM in the sim
concurrently (17 here vs. 3 for plain `topology-multinode.json`), not from
how many of them are actual OCP hosts, so the 3 `ocp-cp-*` VMs take longer to
register than their host count alone would suggest. Override with
`DISCOVERY_TIMEOUT=<seconds>` if 28 minutes still isn't enough (e.g. a
heavily loaded Air org).

If you still hit the timeout, it's not necessarily a real failure, and not a
reason to restart from step 1 — check whether hosts registered right after
the cutoff:

```bash
uv run python -c "
from assisted_common import get_client, cluster_hosts
ai = get_client(quiet=False)
for h in cluster_hosts(ai, 'ocp-nodpu'):
    print(h.get('requested_hostname'), h.get('status'), h.get('status_info'))
"
```

If some/all are missing, retry the wait with the **same full env** as before
(all of `CLUSTER_PROFILE`, `CLUSTER_NAME`, `TOPOLOGY_PATH`, `EXPECTED_HOSTS`,
`API_VIP`, `INGRESS_VIP` — a partial env makes this script look for the
wrong simulation and fail with a confusing "No simulation named
'ocp-cluster' found"):

```bash
uv run 06_wait_for_host_ipv4.py --min-hosts 3 --require-known
```

`No connectivity to the majority of hosts in the cluster` / NTP warnings
while hosts trickle in are expected — see the NTP note in Option A above,
they self-resolve. Once discovery succeeds, resume the orchestrator from
where it left off instead of re-running everything:

```bash
uv run run_cluster.py --from 8 --yes
```

### Check it's working with `oc`

Orchestrator step 11 (`08_verify_cluster.py`) copies `oc` and the kubeconfig
onto `oob-mgmt-server` and prints the real jump-host port/FQDN for the
command below. The kubeconfig already uses
`https://api.ocp-nodpu.dsx.air.local:6443`, so plain `oc get nodes` is
enough — no `--server` / `--insecure-skip-tls-verify`:

```bash
# /etc/hosts (once)
127.0.0.1 api.ocp-nodpu.dsx.air.local
127.0.0.1 console-openshift-console.apps.ocp-nodpu.dsx.air.local
127.0.0.1 oauth-openshift.apps.ocp-nodpu.dsx.air.local

# Terminal 1 — leave this running (step 11 fills in <port> and <jump-host-fqdn>)
ssh -N -L 127.0.0.1:6443:192.168.200.19:6443 -p <port> ubuntu@<jump-host-fqdn>

# Another terminal
export KUBECONFIG=../.cache/kubeconfig.ocp-nodpu
oc get nodes
oc get co
```

To open the OpenShift **web console**, start a **second** SSH tunnel for the
**Ingress VIP** — `192.168.200.20` here, not the plain multinode profile's
`.11` — as the same user as the API tunnel, so SSH still offers the Air UI
key. Do **not** `sudo ssh` — that runs as root, drops your agent, and often
offers `id_ed25519` instead. Keep the API tunnel.

If binding 443/80 fails with "Permission denied", allow unprivileged ports
once, then retry the `ssh` as yourself:

```bash
sudo sysctl net.ipv4.ip_unprivileged_port_start=80

# Terminal 2 — leave this running too (do not include 6443; that is terminal 1)
ssh -N \
  -L 127.0.0.1:443:192.168.200.20:443 \
  -L 127.0.0.1:80:192.168.200.20:80 \
  -p <port> ubuntu@<jump-host-fqdn>
```

Then open `https://console-openshift-console.apps.ocp-nodpu.dsx.air.local`.
The kubeadmin password is in `.cache/` next to the kubeconfig.

`.cache/kubeconfig.ocp-nodpu` is downloaded automatically by `07_install_cluster.py`
(`_download_credentials`) once the install completes — no manual copy needed.
`08_verify_cluster.py` picks the same file by default (`.cache/kubeconfig.$CLUSTER_NAME`),
but a `KUBECONFIG` env var already set in your shell (e.g. left over from an
unrelated IBI/SNO run) silently overrides that and makes step 11 copy the
wrong cluster's kubeconfig to the jump host. `unset KUBECONFIG` before running
step 11, or point it explicitly at `.cache/kubeconfig.ocp-nodpu`.

Expect `ocp-cp-0`, `ocp-cp-1`, `ocp-cp-2` all `Ready` with `master`, and
cluster operators `AVAILABLE=True`.

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

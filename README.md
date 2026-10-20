# OpenShift SNO / OCP on NVIDIA DSX Air

Standard Single Node OpenShift install using an **Assisted Installer**
discovery ISO on NVIDIA DSX / NVIDIA Air — no seed image, no Lifecycle
Agent, no `MachineConfig` partition tricks, no `cdrom`-swap sequence. One
Air node, one boot, one install.

This fork supports **SNO** (`topology.json`) and an R&D **multinode profile**
(`topology-multinode.json`: 3-node HA, 3 control-plane masters). See
[Multinode profile](#multinode-profile-3-node-ha) below.

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
| Air NGC org | `AIR_NGC_ORG` (optional). Display name of the NGC org, e.g. `Ami_RH_NV_TECH_PRTNR`. Sent as `Nv-Ngc-Org`. |
| Assisted Installer offline token | `AI_OFFLINETOKEN` or `AI_OFFLINETOKEN_FILE` |
| Pull secret | `PULL_SECRET_PATH` (path to JSON file) |
| SSH public key (OpenShift nodes) | `SSH_PUBLIC_KEY_PATH` (default `~/.ssh/id_ed25519.pub` or `id_rsa.pub`) — baked into the discovery ISO |
| SSH public key (Air jump host) | The key you add in the **Air UI** (account / simulation SSH keys). `ssh` to `oob-mgmt-server` uses that key, not necessarily `id_ed25519`. |
| OpenShift version | `OCP_VERSION` (**required**, e.g. `4.19`) |
| Cluster name | `CLUSTER_NAME` (default `sno-cluster`, or `ocp-cluster` when `CLUSTER_PROFILE=multinode`) |
| Cluster profile | `CLUSTER_PROFILE` (`sno` default, or `multinode`) |
| Topology manifest | `TOPOLOGY_PATH` (default `topology.json` or `topology-multinode.json`) |
| Expected AI hosts | `EXPECTED_HOSTS` (default: node count in topology) |
| Control plane count | `CONTROL_PLANE_COUNT` (default: CP nodes in topology) |
| Control plane nodes | `CONTROL_PLANE_NODES` (comma-separated override) |
| API VIP (multinode) | `API_VIP` (default `192.168.200.10`) |
| Ingress VIP (multinode) | `INGRESS_VIP` (default `192.168.200.11`) |
| Additional NTP sources | `ADDITIONAL_NTP_SOURCE` (default `192.168.200.1,time.google.com`) |
| Host discovery timeout | `DISCOVERY_TIMEOUT` seconds (default: `max(900, 100 * total topology nodes)`) |
| Control plane node | `CONTROL_PLANE_NODE` (default: first `*cp*` name in topology) |
| Base DNS domain | `BASE_DNS_DOMAIN` (default `dsx.air.local`) |
| Local discovery ISO path | `DISCOVERY_ISO_PATH` / `ISO_PATH` (default `.cache/dsxair-discovery.iso`) |
| Jump host new password | `JUMP_HOST_PASSWORD` (default `redhat`) |
| Jump host factory password | `JUMP_HOST_INITIAL_PASSWORD` (default image value or `nvidia`) |
| `oc` client version | `OC_CLIENT_VERSION` (default `4.12.0`, or `{OCP_VERSION}.0`) |

Shared resolution lives in `scripts/env_config.py`.

## Quick path (happy path)

Run from `scripts/` (scripts import each other by module name):

```bash
cd scripts
export AIR_API_KEY=...          # or AIR_API_KEY_FILE=...
export AI_OFFLINETOKEN=...      # or AI_OFFLINETOKEN_FILE=...
export PULL_SECRET_PATH=~/Downloads/pull-secret
export OCP_VERSION=4.19

uv run 00_create_discovery_iso.py
uv run upload_discovery_iso.py
uv run upload_blank_disk.py
uv run 01_create_simulation.py
uv run 06_wait_for_host_ipv4.py
```

Then finish networking / install in the Assisted Installer UI (Step 5
below). Console-only ISO creation remains a valid fallback if you prefer
not to use `ailib`.

If a simulation named `sno-cluster` already exists, `01_create_simulation.py`
checks whether its nodes' attached discovery ISO still matches
`topology.json`; if it's stale (e.g. after a `--force` ISO rebuild under a
new name) it deletes and re-imports automatically. Pass `--force` yourself
to always recreate regardless. If the simulation is stuck in an `INVALID`
state that isn't a plain cdrom mismatch, delete it in the Air UI and re-run.

## Important: use Air's OOB network, not an `"outbound"` link — link-local IPs don't work here

We hit this for real too, later than the CD-ROM issue: `topology.json` used
to wire `eth0` straight to `"outbound"`, which is the setup that gives you
the `169.254.0.2/24`/`169.254.0.1` addressing referenced throughout this
repo's other docs. That works fine for plain SSH/internet-only labs — but
**not** for this one. Assisted Installer explicitly rejects link-local
addresses (`169.254.0.0/16`) as a machine network. The symptom is exactly
this: the host registers under Host discovery (so it did phone home fine),
but every network column (Active NIC/IPv4/IPv6/MAC) is blank and the
Networking step shows "No subnets are currently available" — the installer
saw the address, recognized it as link-local, and discarded it.

The fix: use Air's **OOB management network** instead. It hands out real,
routable `192.168.200.x/24`-range addresses (via its own DHCP/DNS/NAT on
`oob-mgmt-server`), not link-local ones. `topology.json` now has `"links":
[]` and no `"oob": false` anywhere — per Air's docs, omitting `oob`
entirely defaults it to **on**, which automatically connects the node's
`eth0` to the OOB network with no explicit link needed. That single change
is enough — no other topology fields need to differ.

Note there's a second, unrelated way to end up with the exact same
`169.254.x.x` symptom even *after* switching to the OOB network: if
`topology.json` pins a `management_mac` that doesn't match what Air
actually presents, DHCP fails and the OS self-assigns a link-local address
as a fallback (RFC 3927 APIPA) — same address range, completely different
cause (a stale pinned MAC, not the wrong link type). See "Important: don't
pin `management_mac`" below if you hit `169.254.x.x` again after this fix.

One consequence: you won't know the node's actual IP until after it boots
(it's DHCP-assigned from the `192.168.200.0/24` pool based on MAC). Check it
either from the Assisted Installer's Host discovery table once the columns
populate (they will now, since the address is a valid subnet), or from
Air's node console. `scripts/06_wait_for_host_ipv4.py` polls until that
OOB address appears.

**Don't be alarmed if you see a `169.254.x.x` address while debugging
`oob-mgmt-server` itself** — that's expected and unrelated to the fix above.
`oob-mgmt-server`'s own `eth0` is Air's normal internal uplink (it's how
Air manages that node), separate from the `192.168.200.0/24` OOB subnet it
serves out to everyone else. Checking `ip a` on `oob-mgmt-server` you should
see: `eth0` with a `169.254.x.x` address (normal, Air's internal uplink —
ignore it) and `eth1` with `192.168.200.1` (the actual DHCP/gateway address
for the OOB subnet — this is the healthy sign to look for). The address
that actually matters for your install is the one DHCP hands out to
`sno-cluster` itself (e.g. `192.168.200.2`), not anything on
`oob-mgmt-server`'s `eth0`.

Also worth knowing: this OOB network is still NAT'd/private — outbound
internet access from the node works out of the box, but *inbound* access
from outside Air (your own browser/`oc` hitting the API or console URLs
after install) does not, by default. Once the cluster is up, you'll need to
either open a Service (SSH/HTTPS on the node's port 6443/443) to expose it
externally and point DNS at that, or SSH into `oob-mgmt-server` as a jump
host and run `oc` from there. `scripts/01_create_simulation.py` now sets up
that jump host automatically (see `scripts/04_create_jump_host_service.py`)
so the ssh command is ready and waiting by the time you get to verification —
it's not a blocker for getting the install itself to complete either way.

**First-login password on `oob-mgmt-server`:** the auto-provisioned jump host
ships with user `ubuntu` / factory password `nvidia` and *requires* a password
change on first SSH. Pubkey login uses the SSH key you added in the **Air UI**
(not `SSH_PUBLIC_KEY_PATH`, which is only for the OpenShift nodes). Until the
password is changed, pubkey auth connects but every command fails with
`Password change required but no TTY available`.
`01_create_simulation.py` and `04_create_jump_host_service.py` now run the
bootstrap automatically (via `expect`). Override the new password with
`JUMP_HOST_PASSWORD` (default `redhat`). Re-run manually with
`uv run bootstrap_jump_host.py` if needed.

## Important: don't pin `management_mac` in `topology.json`

This is the *other* way to end up with a rejected `169.254.x.x` machine
network — see the note at the end of the OOB-network section above. That
section's fix (switching from `"outbound"` to the OOB network) is about
picking the right network entirely; this one is about a pinned MAC breaking
DHCP *within* an already-correct OOB setup, causing the node to
self-assign a link-local address as a fallback instead of getting its real
`192.168.200.x` lease.

We hit this for real too: an earlier version of `topology.json` pinned a
specific MAC via `"management_mac": "48:B0:2D:00:00:12"` on the
`sno-cluster` node, matched by a static reservation we'd also hand-added to
`oob-mgmt-server`'s `/etc/dhcp/dhcpd.hosts`. The node never got a DHCP
lease. Digging in with `tcpdump` on `oob-mgmt-server` showed the
`DHCPDISCOVER` traffic was arriving from a *different* MAC than the one
pinned in `topology.json` — Air simply doesn't honor `management_mac` for
nodes on the implicit/auto-provisioned OOB network; the interface Air
actually creates presents its own MAC regardless of what you pin in the
manifest. Both the SDK and the raw API confirmed this: `node.management_mac`
came back `None` on the live node no matter what the topology said.

The fix is to **not set `management_mac` at all** — delete the field
entirely and let Air assign/present whatever MAC it wants. Its own
DHCP server then matches its own interface correctly, since there's no
stale static reservation fighting it. Before/after:

```jsonc
// Before (breaks DHCP on the OOB network — don't do this):
"sno-cluster": {
    ...
    "management_mac": "48:B0:2D:00:00:12",
    ...
}
```

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

**Confirmed fix**, from `oob-mgmt-server`'s DHCP log after dropping
`management_mac` from the manifest: Air assigned its own MAC to the node's
interface, and the DHCP reservation matched it immediately —

```
DHCPACK on 192.168.200.2 to 48:b0:2d:00:00:00
```

— `sno-cluster` got its real `192.168.200.2` OOB lease on the first try,
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

The reason we settled on this instead of flipping the `boot` field back and
forth (an earlier iteration of this project's `scripts/02_attach_discovery_iso.py`
and `scripts/03_boot_to_disk.py` did exactly that): changing `boot`/`cdrom`
via `node.update()` only ever changes
*firmware's device preference*, not what's actually on the disk. If `hd`
already has a real OS on it, setting `boot` to `cdrom`-first and back again
doesn't un-write that disk — you'd still boot the old install. The only
thing that reliably gets you back to a fresh discovery boot is reformatting
the disk itself, which is exactly what `node.rebuild()` does. Once you're
on the blank-disk pattern, the `boot` list itself never needs to change
again — only whether the disk behind `hd` is blank or installed.

## Important: Air images must exist *before* you import the topology

Importing a topology that references missing `cdrom` / `os` image names
fails validation and leaves the simulation `INVALID`. Both of these must
already exist in your org at **import time**:

- `dsxair-discovery-iso` (discovery ISO uploaded from Step 1/2)
- `blank-100g` (sparse blank qcow2 from `upload_blank_disk.py`)

`topology.json` uses Air's real schema shape (`nic_model` / `cpu_mode` /
`secureboot`, `"features": {"uefi": ...}`) rather than older public-doc
placeholders.

## Step 1 — Create the Assisted Installer cluster and download the discovery ISO

Preferred (SaaS API via `ailib`):

```bash
cd scripts
uv run 00_create_discovery_iso.py
```

This creates a SNO cluster + infraenv on `api.openshift.com` and downloads
the discovery ISO to `DISCOVERY_ISO_PATH` (default `.cache/dsxair-discovery.iso`).
Re-runs reuse an existing same-named cluster/infraenv; pass `--force` to
delete and recreate. To tear down only the Assisted Installer objects:

```bash
uv run delete_assisted_cluster.py --yes
```

### Optional fallback — Assisted Installer console

1. Go to `console.redhat.com/openshift/assisted-installer/clusters` →
   **Create cluster**.
2. Select **Datacenter** → **Single Node OpenShift**, choose your OCP
   version and `x86_64`.
3. Set cluster name / base domain (defaults used by scripts:
   `sno-cluster` / `dsx.air.local`).
4. Skip static network configuration — DHCP on Air's OOB network assigns
   `192.168.200.x`.
5. Download the **Discovery ISO** (minimal is fine) and point
   `DISCOVERY_ISO_PATH` at that file before Step 2.

## Step 2 — Upload the discovery ISO to Air

```bash
uv run upload_discovery_iso.py
```

Uploads as Air image `dsxair-discovery-iso` (must match `topology.json`
`"cdrom"`). Skips if the image already exists. There is no `--replace`: Air
rejects clearing/overwriting content on an image already attached to nodes,
and can serve a stale CDROM cache even when it's not. If you need fresh
content, upload under a new `--name` and update `topology.json`'s `"cdrom"`
field to match.

## Step 3 — Create and upload `blank-100g`

```bash
uv run upload_blank_disk.py
```

Creates a sparse local 100G qcow2 (if needed) and uploads it as Air image
`blank-100g` (must match `topology.json` `"os"`). Skips if present — its
content never needs to change.

## Step 4 — Import the topology and boot the node

```bash
uv run 01_create_simulation.py
```

Imports `topology.json` and starts the simulation. Air auto-provisions
`oob-mgmt-switch-leaf-1` and `oob-mgmt-server` for the default OOB network.
The script also sets up the SSH jump host onto `oob-mgmt-server` and prints
the `ssh` command (re-printable later with
`04_create_jump_host_service.py`).

If a simulation with this name already exists, its nodes' attached cdrom
images are checked against `topology.json`; a stale one (left over from a
previous ISO name) is deleted and re-imported automatically. `--force`
always recreates, even when it looks aligned. If the simulation is stuck in
an `INVALID` state that isn't a plain cdrom mismatch, delete it in the Air
UI and re-run.

To force discovery again after a failed install, rebuild the node
(`node.rebuild()`) rather than toggling `boot`/`cdrom` — see the boot-order
note above.

## Step 5 — Wait for host discovery, then install

```bash
uv run 06_wait_for_host_ipv4.py
```

Polls Assisted Installer until a host for the cluster shows an OOB IPv4 in
`192.168.200.0/24`. That proves Air networking + discovery ISO phone-home
are working. The script does **not** start the install.

Then in `console.redhat.com/openshift/assisted-installer/clusters/<id>`:

1. Confirm Host discovery shows the real `192.168.200.x` address — note it;
   it is your API/Ingress VIP for SNO.
2. On **Networking**, select the `192.168.200.0/24` machine network. Set
   **API VIP** and **Ingress VIP** to the node's own address.
3. Point DNS (`api.<cluster>.<domain>` and `*.apps.<cluster>.<domain>`) at
   that same address — **not** `169.254.0.2`.
4. Wait for validations to turn green, then **Install cluster**.
5. Download `kubeconfig` and the `kubeadmin` password when finished.

## Step 6 — Verify

```bash
export KUBECONFIG=~/Downloads/kubeconfig
oc get nodes
oc get clusterversion
```

This only works directly from your laptop if you can route to
`192.168.200.x` — which you generally can't. Options, easiest first:

- SSH into `oob-mgmt-server` using the command
  `01_create_simulation.py` printed (or
  `uv run 04_create_jump_host_service.py`), then run `oc` from there.
  `scp`/paste your `kubeconfig` over first.
- Or add a local port-forward, e.g. `-L 6443:<sno-cluster-ip>:6443`, and
  point `KUBECONFIG`'s server URL at `https://localhost:6443`.
- Or create an Air **Service** (HTTPS / port `6443` on the node's `eth0`)
  and point DNS / kubeconfig at that public FQDN.

## Multinode profile (3-node HA)

R&D profile for greenfield **3 control-plane HA** on DSX Air. Uses the same
blank-disk boot pattern as SNO; adds Assisted Installer VIPs and host role
assignment (all CP nodes are `master`).

**Resource budget** (per node in `topology-multinode.json`):

| Node | vCPU | RAM | Disk |
|------|------|-----|------|
| `ocp-cp-0` | 16 | 64 GB | 100 GB |
| `ocp-cp-1` | 16 | 64 GB | 100 GB |
| `ocp-cp-2` | 16 | 64 GB | 100 GB |

Tear down other **ACTIVE** simulations in the Air UI before importing
`topology-multinode.json` — Air refuses a second import with the same name,
and quota is shared across the org.

### Multinode quick path

#### Option A — `run_cluster.py` orchestrator (recommended)

`scripts/run_cluster.py` wraps every step below (both this multinode profile
and the SNO flow) behind one entry point, so you don't have to copy-paste
each `uv run ...` command by hand:

```bash
cd scripts
export CLUSTER_PROFILE=multinode
export CLUSTER_NAME=ocp-cluster
export OCP_VERSION=4.19
export EXPECTED_HOSTS=3
# AIR_API_KEY, AI_OFFLINETOKEN, PULL_SECRET_PATH ...

uv run run_cluster.py              # interactive menu — pick a step, a range, or 'all'
uv run run_cluster.py --list       # print the numbered steps and exit
uv run run_cluster.py --run 3-6    # run a specific range non-interactively
uv run run_cluster.py --all --yes  # run everything, accepting every prompt's default
uv run run_cluster.py --recover ocp-cp-1   # ad hoc: rebuild + re-attach discovery ISO
```

It runs the exact scripts listed in Option B below (nothing new happening
under the hood), tracks per-step pass/fail for the session so you can retry
just the step that failed, and on the multinode profile it automates the
"mint a fresh ISO name and update every node's `cdrom`" step that's manual
in Option B — generating `dsxair-discovery-<timestamp>`, uploading it, and
rewriting `topology-multinode.json` for you. Every prompt has a safe default
(shown when you answer, or picked automatically under `--yes`/non-interactive
stdin), so it's CI-friendly too. Full reference: `scripts/SCRIPTS.md`.

#### Option B — manual, step by step

```bash
cd scripts
export CLUSTER_PROFILE=multinode
export CLUSTER_NAME=ocp-cluster
export OCP_VERSION=4.19
export EXPECTED_HOSTS=3
# AIR_API_KEY, AI_OFFLINETOKEN, PULL_SECRET_PATH ...

uv run delete_assisted_cluster.py --yes
uv run 00_create_discovery_iso.py --force --profile multinode

ISO_NAME="dsxair-discovery-$(date +%s)"
uv run upload_discovery_iso.py --name "$ISO_NAME"
# Set all topology cdrom fields to $ISO_NAME, then:
uv run upload_blank_disk.py
uv run 01_create_simulation.py
uv run 06_wait_for_host_ipv4.py --require-known --min-hosts 3
uv run assign_host_roles.py          # optional; 07 also assigns roles
uv run 07_install_cluster.py --configure-only   # gate before install
uv run 07_install_cluster.py
```

After install, run orchestrator **step 11** (`08_verify_cluster.py`). It
copies `oc` and kubeconfig onto `oob-mgmt-server`, writes cluster names
into that host's `/etc/hosts`, and prints the tunnel commands below with
the real jump-host port and FQDN filled in.

From this laptop, point the API hostname at the tunnel, then forward the
**API VIP** (not a node IP) in **terminal 1**. Run `ssh` as yourself so it
can use the key you added in the Air UI (the same key that already logs
you into the jump host). That `ssh -N` sits until you Ctrl+C — run `oc` in
another terminal. The kubeconfig already uses
`https://api.ocp-cluster.dsx.air.local:6443`, so plain `oc get nodes` is
enough — no `--server` / `--insecure-skip-tls-verify`.

```bash
# /etc/hosts (once)
127.0.0.1 api.ocp-cluster.dsx.air.local
127.0.0.1 console-openshift-console.apps.ocp-cluster.dsx.air.local
127.0.0.1 oauth-openshift.apps.ocp-cluster.dsx.air.local

# Terminal 1 — leave this running (step 11 fills in <port> and <jump-host-fqdn>)
ssh -N -L 127.0.0.1:6443:192.168.200.10:6443 -p <port> ubuntu@<jump-host-fqdn>

# Another terminal
export KUBECONFIG=../.cache/kubeconfig.ocp-cluster
oc get nodes
```

To open the OpenShift **web console**, start a **second** SSH tunnel for the
**Ingress VIP**, as the same user as the API tunnel, so SSH still offers the
Air UI key. Do **not** `sudo ssh` — that runs as root, drops your agent, and
often offers `id_ed25519` instead. Keep the API tunnel.

If binding 443/80 fails with "Permission denied", allow unprivileged ports
once, then retry the `ssh` as yourself:

```bash
sudo sysctl net.ipv4.ip_unprivileged_port_start=80

# Terminal 2 — leave this running too (do not include 6443; that is terminal 1)
ssh -N \
  -L 127.0.0.1:443:192.168.200.11:443 \
  -L 127.0.0.1:80:192.168.200.11:80 \
  -p <port> ubuntu@<jump-host-fqdn>
```

Then open `https://console-openshift-console.apps.ocp-cluster.dsx.air.local`.
The kubeadmin password is in `.cache/` next to the kubeconfig.

Default VIPs: `API_VIP=192.168.200.10`, `INGRESS_VIP=192.168.200.11`.
Override if they conflict with DHCP leases.

**Fresh ISO name:** After `00 --force`, upload under a **new** Air image name
and update every node's `cdrom` in `topology-multinode.json`. There is no
`--replace` — Air rejects overwriting content on an image already in use by
nodes and may serve a stale CDROM cache even when it's not in use.

### Phased validation

| Phase | Success | If it fails |
|-------|---------|-------------|
| P1 Discovery | 3 hosts in AI with OOB `192.168.200.x` | Topology, ISO, OOB networking |
| P2 Roles + VIPs | AI networking validations pass; cluster `ready` | `assign_host_roles.py`, VIP conflicts |
| P3 Install | Cluster `installed` | `09_recover_to_discovery.py --node <name>` |
| P4 Verify | `oc get nodes` → 3 Ready | Step 11 tunnels to API VIP; `/etc/hosts` for console |

Per-node recovery:

```bash
uv run 09_recover_to_discovery.py --all --reset-ai
```

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

1. **VIP collision.** Default `API_VIP=192.168.200.10` / `INGRESS_VIP=192.168.200.11`
   are already statically claimed by `ns-spine-02-02` and `ocp-cp-0` in this
   topology. Use free addresses instead — everything `.2`–`.18` is taken by
   fabric/compute nodes, so e.g. `API_VIP=192.168.200.19`,
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
the same as the plain multinode profile's quota warning above, just with a
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
export OCP_VERSION=4.19
export EXPECTED_HOSTS=3
export TOPOLOGY_PATH=Topology-NoDPU.json
export API_VIP=192.168.200.19
export INGRESS_VIP=192.168.200.20
export AIR_API_KEY_FILE=~/.config/dsx-air/air-api-key
export AI_OFFLINETOKEN_FILE=~/.config/dsx-air/ai-offlinetoken
export PULL_SECRET_PATH=~/.config/dsx-air/pull-secret.json

uv run run_cluster.py --all --yes
```

This runs the same 11 steps as the plain multinode profile (see `--list`):
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
export OCP_VERSION=4.19
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

Expect `ocp-cp-0`, `ocp-cp-1`, `ocp-cp-2` all `Ready` with `master`, and
cluster operators `AVAILABLE=True`. For the web console, forward the
**Ingress VIP** (`192.168.200.20`, not `.11`) instead of the plain
multinode profile's `.11` in a second tunnel, then open
`https://console-openshift-console.apps.ocp-nodpu.dsx.air.local`.

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

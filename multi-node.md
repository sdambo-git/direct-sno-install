# Multinode profile (3-node HA)

Split out of the main [README.md](README.md), which keeps the prerequisites,
auth inputs, the SNO flow, and the NVIDIA DSX fabric (`Topology-NoDPU.json`)
variant.

## Contents

- [Multinode quick path](#multinode-quick-path)
  - [Option A — `run_cluster.py` orchestrator (recommended)](#option-a--run_clusterpy-orchestrator-recommended)
  - [Option B — manual, step by step](#option-b--manual-step-by-step)
- [Phased validation](#phased-validation)

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

## Multinode quick path

### Option A — `run_cluster.py` orchestrator (recommended)

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

### Option B — manual, step by step

```bash
cd scripts
export CLUSTER_PROFILE=multinode
export CLUSTER_NAME=ocp-cluster
export OCP_VERSION=4.22
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
**Ingress VIP** (`192.168.200.11`), as the same user as the API tunnel. The
full command — including the unprivileged-ports fix if binding 443/80 fails
with "Permission denied" — is in [*Check it's working with `oc`*](README.md#check-its-working-with-oc)
in the README's NVIDIA DSX fabric section; it's identical except this
profile's Ingress VIP is `192.168.200.11`, not `.20`.

Default VIPs: `API_VIP=192.168.200.10`, `INGRESS_VIP=192.168.200.11`.
Override if they conflict with DHCP leases.

**Fresh ISO name:** After `00 --force`, upload under a **new** Air image name
and update every node's `cdrom` in `topology-multinode.json`. There is no
`--replace` — Air rejects overwriting content on an image already in use by
nodes and may serve a stale CDROM cache even when it's not in use.

## Phased validation

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


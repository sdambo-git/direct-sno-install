# SNO install — manual walkthrough

Split out of the main [README.md](README.md), which keeps the prerequisites,
auth inputs, the gotchas, and the NVIDIA DSX fabric (`Topology-NoDPU.json`)
flow. The 3-node HA profile lives in [multi-node.md](multi-node.md).

These steps run the SNO flow by hand — Step 1 = orchestrator step 2,
Step 2 = 3, Step 3 = 4, Step 4 = 6, Step 5 = 7–10, Step 6 = 11. Use them
when a specific step fails and you want to re-run or debug just that piece,
or when you'd rather drive the Assisted Installer UI yourself.

## Contents

- [Step 1 — Create the Assisted Installer cluster and download the discovery ISO](#step-1--create-the-assisted-installer-cluster-and-download-the-discovery-iso)
  - [Optional fallback — Assisted Installer console](#optional-fallback--assisted-installer-console)
- [Step 2 — Upload the discovery ISO to Air](#step-2--upload-the-discovery-iso-to-air)
- [Step 3 — Create and upload `blank-100g`](#step-3--create-and-upload-blank-100g)
- [Step 4 — Import the topology and boot the node](#step-4--import-the-topology-and-boot-the-node)
- [Step 5 — Wait for host discovery, then install](#step-5--wait-for-host-discovery-then-install)
- [Step 6 — Verify](#step-6--verify)

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
(`node.rebuild()`) rather than toggling `boot`/`cdrom` — see the
[boot-order note](README.md#important-boot-order-stays-hd-cdrom--dont-toggle-it)
in the README.

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


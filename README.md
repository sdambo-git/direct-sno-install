# OpenShift on DSX Air — Lab guide

Two paths:

1. **Greenfield** — `dsx-air deploy --spec examples/ha-3cp-2w.yaml` (3 control
   plane + 2 workers). Topology is generated; do not hand-edit JSON.
2. **Operate existing** — shared **`ocp-cluster`** sim. No install, no delete
   unless you pass `destroy --spec` / `--replace`.

Numbered scripts: [README.md](scripts/README.md) and [scripts/SCRIPTS.md](scripts/SCRIPTS.md).

## Greenfield (spec)

Put tokens in files (never in git). The example spec points at:

| Spec key | Default path |
|----------|----------------|
| `auth.air_api_key_file` | `~/.config/dsx-air/air-api-key` |
| `auth.ai_offlinetoken_file` | `~/.config/dsx-air/ai-offlinetoken` (refresh at `console.redhat.com/openshift/token` if deploy hits HTTP 400) |
| `auth.pull_secret_file` | `~/.config/dsx-air/pull-secret.json` |
| `auth.ssh_public_key_file` | `~/.ssh/id_ed25519.pub` |

CLI overrides: `--sim`, `--cluster`, `--control-plane`, `--workers`, `--ocp-version`.
Host discovery waits `max(20, 8 × host count)` minutes (40m for 3+2); override with `--discovery-timeout` (minutes). Zero hosts for 20 minutes fails fast; if any host appears, the long wait continues. NTP / majority-connectivity `insufficient` is a warning on Air — wait for known/ready. As soon as a host hostname matches `ocp-cp-*` / `ocp-worker-*`, Assisted `master`/`worker` is set from the topology (not arrival order).

```bash
uv sync
uv run dsx-air deploy --spec examples/ha-3cp-2w.yaml
uv run dsx-air tunnel
uv run dsx-air console
uv run dsx-air workload --follow
```

SNO seed for image-based install: [Image-based installation](#image-based-installation-on-dsx-air).

`deploy` remembers the spec in `.cache/last-spec`, so `tunnel` / `start` /
`status` / `console` without `--spec` use that simulation (not the shared
`ocp-cluster` lab). Pass `--spec` anytime to select a different lab.

`console` starts SOCKS through the jump host and launches host Chromium/Chrome.
Chrome SOCKS5 resolves names **on the jump host**, so `start`/`console` write
API/Console entries into jump-host `/etc/hosts` (not the laptop). `--print-only`
prints commands without launching.

With 2+ Ready dedicated workers (not control-plane), `workload` starts a **ring**
of traffic: 2 workers → both directions; 3 workers → 0→1, 1→2, 2→0.

```bash
uv run dsx-air workload                      # iperf3, stream logs when Ready
uv run dsx-air workload --no-follow          # start only
uv run dsx-air workload --kind web --interval 2
uv run dsx-air workload --stop
```

Destroy (TTY prompt; `--force` for scripts):

```bash
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml --sim
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml --cluster --force
uv run dsx-air deploy --spec examples/ha-3cp-2w.yaml --replace
```

## Image-based installation on DSX Air

Two simulations, two ISOs. The **seed** is an Assisted SNO. The **target** boots a live ISO that restores that seed, then a site config finishes the cluster. `dsx-air deploy` is only the seed. Do not run Assisted `06`/`07` on the target.

Needs `uv`, an Air API key, a pull secret, `~/.ssh/id_ed25519.pub`, and `openshift-install` **4.22.14** as `./openshift-install` or on `PATH` (same version as the seed). `xorriso` is used when applying the config ISO over SSH.

| Lab | Spec | Role |
|-----|------|------|
| `dsx-sno-ibi` | `examples/sno.yaml` | Seed. 300G disk, ~100GiB root + containers partition. |
| `dsx-ibi-target` | `examples/ibi-target.yaml` | Target. New sim. Do not delete the seed to rebuild this. |

### 1. Install the seed

```bash
uv sync
uv run dsx-air deploy --spec examples/sno.yaml
uv run dsx-air tunnel --spec examples/sno.yaml
```

Run the printed `ssh -N -L` in another terminal. SNO forwards to the node OOB address (`192.168.200.2`), not the HA VIP `.10`.

```bash
export KUBECONFIG=$PWD/.cache/kubeconfig.ocp
oc get nodes
```

The Assisted extra MachineConfig leaves about 100GiB for RHCOS and the rest for `/var/lib/containers` (`vda5`, label `var-lib-containers`). An already-filled `vda4` cannot be shrunk; `deploy --replace` is required for a new seed.

### 2. Generate the seed image

On the seed cluster, install Lifecycle Agent:

```bash
oc apply -f ibi/ola_ns.yaml -f ibi/ola_og.yaml -f ibi/ola_sub.yaml
oc get csv -n openshift-lifecycle-agent
```

Lifecycle Agent pushes `spec.seedImage` from `ibi/seedgenerator.yaml` (currently `quay.io/sdambo/ocp-seed:4.22.14`). It will not start until a Secret named `seedgen` exists in `openshift-lifecycle-agent`. The cluster pull secret is not that Secret. Log in with an account that can **push** to that repository, then create the Secret from the auth file. Do not commit it (`scripts/seedgen.yaml` is gitignored for this):

```bash
podman login quay.io
oc create secret generic seedgen \
  -n openshift-lifecycle-agent \
  --from-file=seedAuth="${XDG_RUNTIME_DIR:-$HOME/.config}/containers/auth.json"
```

If `podman` wrote `$HOME/.config/containers/auth.json` instead, pass that path. Then apply the generator. A failed `SeedGenerator` does not retry; delete it before applying again:

```bash
oc delete seedgenerator seedimage --ignore-not-found
oc apply -f ibi/seedgenerator.yaml
oc get seedgenerator seedimage -o yaml
```

The seed node reboots and the API drops while the image is built and pushed. When it returns, `status.conditions` type `SeedGenCompleted` must be `True`. The image must be pullable from the target. Leave `dsx-sno-ibi` in place. You do not need it running after the image is pushed.

### 3. Build the live ISO

```bash
uv run dsx-air ibi image --spec examples/sno.yaml \
  --seed-image quay.io/<user>/ocp-seed:4.22.14 \
  --seed-version 4.22.14
```

Writes `ibi-iso-workdir/rhcos-ibi.iso` (gitignored; contains the pull secret). `extraPartitionStart` must be `100G`. The installer rejects `100GiB`.

### 4. Create the target

```bash
uv run dsx-air ibi target --spec examples/ibi-target.yaml
```

Uploads the live ISO as `rhcos-ibi-4.22.14`, uses `blank-300g`, imports sim `dsx-ibi-target`, and starts it. Air adds `oob-mgmt-server` (DHCP `192.168.200.0/24`, jump host). Boot stays `["hd", "cdrom"]`: a blank disk falls through to the live ISO.

`--replace` deletes **only** `dsx-ibi-target` and creates it again. It does not delete the seed.

```bash
uv run dsx-air start --spec examples/ibi-target.yaml
uv run dsx-air ibi wait-oob --spec examples/ibi-target.yaml
```

The Air VGA console is often blank on this ISO. Check the sim from the laptop, then the install journal on the node.

**Check the sim is up.** `wait-oob` prints the jump SSH command when `192.168.200.2` answers ping. `ssh` to that address can still say `connection refused` while the live ISO is installing.

```bash
uv run dsx-air ibi wait-oob --spec examples/ibi-target.yaml
# from the jump host printed above:
ping -c 2 192.168.200.2
```

**Check IBI preparation finished.** The jump host does not have the node SSH key. From the laptop:

```bash
ssh -o ProxyJump=ubuntu@<jump-host>:<port> core@192.168.200.2
```

On the node, wait until this shows `IBI preparation process finished successfully!`:

```bash
journalctl -b | grep -E 'IBI preparation|Images Failed|Finished SNO'
```

Expected lines, in order:

```text
IBI preparation process has started
Images Failed to Pull: 0
Pre-cached images successfully.
IBI preparation process finished successfully!
Finished SNO Image-based Installation.
```

`Images Failed to Pull` must be `0`. Then confirm the disk:

```bash
lsblk -o NAME,SIZE,LABEL,MOUNTPOINT
```

`vda4` is root and `vda5` is the containers partition. Do not build the config ISO or run `ibi apply-config` before the finished line. A target that already completed this keeps the seed that was current at boot. A new Quay tag is pulled only by a new live ISO on a new or rebuilt target.

### 5. Build the configuration ISO

```bash
uv run dsx-air ibi config-image --spec examples/ibi-target.yaml
```

Writes `ibi-config-iso-workdir/imagebasedconfig.iso` (label `cluster-config`) and `ibi-config-iso-workdir/auth/kubeconfig`. That kubeconfig is the **target** cluster. `.cache/kubeconfig.ocp` is the seed and will not match after recertification.

If `create config-image` fails because a previous state file is already consumed, remove `ibi-config-iso-workdir/.openshift_install_state.json` only when you intend to regenerate the ISO and kubeconfig.

### 6. Apply site config

Air will not change the CD-ROM while a checkpoint exists, and deleting that checkpoint can reset the disk to `blank-300g`. Apply the config over SSH instead:

```bash
uv run dsx-air ibi apply-config --spec examples/ibi-target.yaml
```

Or by hand, after `scp` of `cluster-configuration` to `core`:

```bash
sudo mkdir -p /opt/openshift
sudo rm -rf /opt/openshift/cluster-configuration
sudo cp -a ~/cluster-configuration /opt/openshift/cluster-configuration
sudo chmod -R a+rX /opt/openshift/cluster-configuration
journalctl -b -f
```

Lifecycle Agent accepts that directory or a CD-ROM labeled `cluster-config`. It leaves the “waiting for cluster-config” loop and brings up the API on `192.168.200.2:6443`.

`ibi attach-config` swaps the CD-ROM. Use it only on a sim with no checkpoints. Do not run it while the sim is `BOOTING`.

### 7. Use the cluster and the web console

The target has the usual OpenShift web console once the console operator is up. The Air VGA window on `ocp-cp-0` is not that console and is often blank on the live ISO.

```bash
export KUBECONFIG=$PWD/ibi-config-iso-workdir/auth/kubeconfig
uv run dsx-air tunnel --spec examples/ibi-target.yaml
oc get nodes
oc get co console
```

Expect the node `Ready` with `control-plane,master`. Pass `--spec examples/ibi-target.yaml` so the tunnel uses this sim’s jump host (`192.168.200.2` for both API and apps). `.cache/last-spec` may still point at the seed. `.cache/kubeconfig.ocp` is the seed and will not log into this cluster.

Open the console from the laptop:

```bash
uv run dsx-air console --spec examples/ibi-target.yaml
```

That starts SOCKS on `127.0.0.1:1080` through the target jump host and launches Chrome at:

`https://console-openshift-console.apps.ocp.dsx.air.local`

| Field | Value |
|-------|--------|
| Username | `kubeadmin` |
| Password | `ibi-config-iso-workdir/auth/kubeadmin-password` |

That password file is created with the config ISO. Do not use `.cache/kubeadmin-password.ocp` (seed). If the page does not load, `oc get co console` and `oc get pods -n openshift-console` show whether the route is up yet.

## Operate existing `ocp-cluster`

Operate the shared **`ocp-cluster`** simulation. Do not run `deploy --replace`
against this name unless you intend to wipe it.

## What this lab is

| Item | Value |
|------|-------|
| Simulation | `ocp-cluster` |
| Simulation ID | `c5a70d5b-22cc-42d7-8931-90d0d2f1c45b` |
| OpenShift | 4.19.x, 3-node HA |
| Nodes | `ocp-cp-0` … `ocp-cp-2` (control-plane, master, worker labels) |
| API VIP | `192.168.200.10` |
| Operators | NFD, NMState, SR-IOV ([phase 1 results](docs/air-ansible-phase1-results.md)) |
| Known gap | `0` `SriovNetworkNodePolicy` — no SR-IOV NICs in the sim |

## Before you start

| Requirement | Details |
|-------------|---------|
| Org | **Ami org** (`Ami_RH_NV_TECH_PRTNR`) — personal-org API keys will not see this sim |
| API key | NGC Personal API Key with **NVIDIA Air** enabled; regenerate after role changes |
| Kubeconfig | `.cache/kubeconfig.ocp-cluster` from the deployment (gitignored) |
| Tools | `uv`, `oc` on PATH |
| Do **not** run | `00`–`07`, `upload_discovery_iso.py`, `upload_blank_disk.py` |

## Setup

```bash
git clone <repo-url> direct-sno-install
cd direct-sno-install
uv sync

export CLUSTER_PROFILE=multinode
export AIR_API_KEY=...    # Ami org key

mkdir -p .cache
cp /path/from/deployment/kubeconfig.ocp-cluster .cache/
```

Confirm the simulation appears in the Air UI under Ami's org before proceeding.

## Run the lab (two terminals)

**Terminal 2 — API tunnel (leave open)**

```bash
cd direct-sno-install
export CLUSTER_PROFILE=multinode
export AIR_API_KEY=...

uv run dsx-air tunnel
# Copy and run the printed ssh -N -L ... command in this terminal
```

Optional check:

```bash
uv run dsx-air tunnel --check
```

**Terminal 1 — CLI**

```bash
cd direct-sno-install
export CLUSTER_PROFILE=multinode
export AIR_API_KEY=...

uv run dsx-air start      # if sim INACTIVE; idempotent if already ACTIVE
uv run dsx-air status     # exit 0 = ready; read NEXT: if blocked
uv run dsx-air demo       # full health check
```

Exit code `0` means demo-ready: sim ACTIVE, API reachable via tunnel, 3/3 nodes
Ready.

## Commands reference

| Command | Mutates? | Purpose |
|---------|----------|---------|
| `deploy --spec` | Yes | Greenfield Assisted + Air install from YAML |
| `destroy --spec` | Yes | Delete Air sim and/or Assisted cluster (TTY prompt) |
| `recover --spec` | Yes | Rebuild disks to discovery ISO |
| `console --spec` | Yes (local SSH/Chrome) | SOCKS + host Chrome to Web Console |
| `start` | Yes | Start sim + jump host bootstrap |
| `status` | No | Full readiness report + `NEXT:` |
| `tunnel` | No | Print SSH `-L 127.0.0.1:6443:API_VIP:6443` command |
| `tunnel --check` | No | Probe `https://127.0.0.1:6443/version` |
| `cluster` | No | `oc get nodes`, clusterversion, MCPs |
| `operators` | No | NFD / NMState / SR-IOV CSVs + pod summary |
| `workload` | Yes | iperf3 or HTTP ring between Ready workers |
| `demo` | No | Compact status + cluster + operators |

## What success looks like

- `dsx-air status` or `dsx-air demo` exits `0`
- 3/3 nodes Ready, cluster version 4.19.x
- Operator CSVs: `Succeeded`
- Simulation ID unchanged in Air UI after CLI runs

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| 403 on `upload_discovery_iso.py` | Wrong workflow — use this guide, not the README install path |
| `tunnel` looks up `ocp-cluster` | Pass `--spec examples/ha-3cp-2w.yaml` (same file as deploy). After a new deploy, `tunnel` uses `.cache/last-spec` automatically. |
| API unreachable / connection refused | Start the tunnel in Terminal 2; confirm sim is ACTIVE |
| `operators` / `workload` TLS handshake timed out | Do not use a raw GET to 127.0.0.1. `dsx-air` now calls `oc get --raw /version` with the same kubeconfig as `oc` (`api.<cluster>.<domain>` in /etc/hosts). |
| ImagePullBackOff on `workload` | Default image is `registry.redhat.io/ubi9/ubi` (cluster pull secret). Re-run `uv run dsx-air workload --replace`. Override with `DSX_WORKLOAD_IMAGE`. |
| `oc` not found | Install the OpenShift CLI |
| Jump host not ready | `uv run dsx-air start` |
| Sim not visible in Air UI | Wrong org or API key — confirm Ami org key, not personal |
| expect is not on PATH (needed for jump-host password bootstrap). | run `sudo dnf install expect` |

## Sim protection

Operate-existing commands (`start`, `status`, `demo`, `tunnel`) never delete
`ocp-cluster`. Greenfield wipe is explicit: `dsx-air destroy --spec` or
`deploy --replace`. Off-lab recovery: `dsx-air recover` /
`09_recover_to_discovery.py`.

## Alternative invocation

```bash
cd scripts && uv run python -m dsx_air demo
```

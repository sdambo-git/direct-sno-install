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
| `auth.ai_offlinetoken_file` | `~/.config/dsx-air/ai-offlinetoken` |
| `auth.pull_secret_file` | `~/.config/dsx-air/pull-secret.json` |
| `auth.ssh_public_key_file` | `~/.ssh/id_ed25519.pub` |

CLI overrides: `--sim`, `--cluster`, `--control-plane`, `--workers`, `--ocp-version`.
Host discovery waits `max(20, 8 × host count)` minutes (40m for 3+2); override with `--discovery-timeout` (minutes). Zero hosts for 20 minutes fails fast; if any host appears, the long wait continues. NTP / majority-connectivity `insufficient` is a warning on Air — wait for known/ready. As soon as a host hostname matches `ocp-cp-*` / `ocp-worker-*`, Assisted `master`/`worker` is set from the topology (not arrival order).

```bash
uv sync
uv run dsx-air deploy --spec examples/ha-3cp-2w.yaml
uv run dsx-air console --spec examples/ha-3cp-2w.yaml
```

`console` starts SOCKS through the jump host and launches host Chromium/Chrome.
Chrome SOCKS5 resolves names **on the jump host**, so `start`/`console` write
API/Console entries into jump-host `/etc/hosts` (not the laptop). `--print-only`
prints commands without launching.

Destroy (TTY prompt; `--force` for scripts):

```bash
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml --sim
uv run dsx-air destroy --spec examples/ha-3cp-2w.yaml --cluster --force
uv run dsx-air deploy --spec examples/ha-3cp-2w.yaml --replace
```

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
| `tunnel` exits 1 | Pass `--spec examples/ha-3cp-2w.yaml`. Key is `~/.config/dsx-air/air-api-key` (or `AIR_API_KEY`). The old "Set AIR_API_KEY" line also meant jump host not ready. |
| API unreachable / connection refused | Start the tunnel in Terminal 2; confirm sim is ACTIVE |
| `oc` not found | Install the OpenShift CLI |
| Jump host not ready | `uv run dsx-air start` |
| Sim not visible in Air UI | Wrong org or API key — confirm Ami org key, not personal |

## Sim protection

Operate-existing commands (`start`, `status`, `demo`, `tunnel`) never delete
`ocp-cluster`. Greenfield wipe is explicit: `dsx-air destroy --spec` or
`deploy --replace`. Off-lab recovery: `dsx-air recover` /
`09_recover_to_discovery.py`.

## Alternative invocation

```bash
cd scripts && uv run python -m dsx_air demo
```

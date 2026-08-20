# OpenShift on DSX Air — Lab guide

Operate the shared **`ocp-cluster`** simulation in Ami's org. No install, no sim
delete.

Full greenfield install docs: [README.md](README.md) and
[scripts/SCRIPTS.md](scripts/SCRIPTS.md).

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
| Kubeconfig | `.cache/kubeconfig.ocp-cluster` from the maintainer (gitignored) |
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
cp /path/from/maintainer/kubeconfig.ocp-cluster .cache/
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
| `tunnel` exits 1, asks for `AIR_API_KEY` | Export an Ami-org key; regenerate if roles were recently added |
| API unreachable / connection refused | Start the tunnel in Terminal 2; confirm sim is ACTIVE |
| `oc` not found | Install the OpenShift CLI |
| Jump host not ready | `uv run dsx-air start` |
| Sim not visible in Air UI | Wrong org or API key — confirm Ami org key, not personal |

## Sim protection

`dsx-air` never deletes, re-imports, or replaces `ocp-cluster`. Off-lab recovery
uses `09_recover_to_discovery.py` (see [SCRIPTS.md](scripts/SCRIPTS.md)), not
the demo CLI.

## Alternative invocation

```bash
cd scripts && uv run python -m dsx_air demo
```

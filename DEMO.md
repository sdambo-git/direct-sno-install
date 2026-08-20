# Demo: OpenShift on DSX Air (Ami / Shahar meeting)

One-page guide for screen-sharing a **working multinode lab** without hunting
through numbered install scripts. Uses the existing `ocp-cluster` simulation only
— no re-install, no sim delete/import.

Full install docs remain in [README.md](README.md) and [scripts/SCRIPTS.md](scripts/SCRIPTS.md).

## Prerequisites

- Simulation **`ocp-cluster`** already installed (3-node HA multinode profile).
- Kubeconfig at `.cache/kubeconfig.ocp-cluster` (from `07_install_cluster.py`).
- `uv sync` from repo root; `oc` in PATH on your laptop.
- `AIR_API_KEY` for `start` / Air sections of `status` (not required for `demo` once the sim is up and tunnel is running).

```bash
export CLUSTER_PROFILE=multinode
export AIR_API_KEY=...   # optional for demo if sim already ACTIVE
```

## Demo eve setup (two terminals)

**Terminal 2 — keep tunnel open**

```bash
cd /path/to/direct-sno-install
export CLUSTER_PROFILE=multinode
uv run dsx-air tunnel
# copy/paste the printed ssh -N -L ... command
```

**Terminal 1 — drive the demo**

```bash
export CLUSTER_PROFILE=multinode
uv run dsx-air start      # if sim INACTIVE; idempotent if already ACTIVE
uv run dsx-air status     # read NEXT: line if anything blocked
uv run dsx-air demo       # compact status + cluster + operators
```

Exit code `0` = demo-ready (sim ACTIVE, API via tunnel, 3/3 nodes Ready).

## Command cheat sheet

| Command | Mutates? | Purpose |
|---------|----------|---------|
| `start` | Yes | Start sim + jump host bootstrap |
| `status` | No | Full readiness report + `NEXT:` |
| `tunnel` | No | Print SSH `-L 127.0.0.1:6443:API_VIP:6443` command |
| `tunnel --check` | No | Probe `https://127.0.0.1:6443/version` |
| `cluster` | No | `oc get nodes`, clusterversion, MCPs |
| `operators` | No | NFD / NMState / SR-IOV CSVs + pod summary |
| `demo` | No | Aggregate for the call |

## Talking points (narrative)

- **Proven:** 3-node OpenShift **4.19** on DSX Air; Assisted Installer multinode path works.
- **Operators:** Phase 1 Ansible playbooks deployed NFD, NMState, and SR-IOV operator successfully ([results](docs/air-ansible-phase1-results.md)).
- **Expected gap:** `0` `SriovNetworkNodePolicy` — no SR-IOV NICs in the sim; E2E rail/GPU work waits on hardware emulation.
- **Blocker:** NVIDIA HOST (x86) + ConnectX-8 plugin manifests for org `Ami_RH_NV_TECH_PRTNR` — required before Spectrum-X NIC emulation on Air.
- **Partnership:** Repo shared with Shahar; discuss SDK/marketplace path and what “good” looks like for joint customer demos.

## Sim protection

The demo CLI **never** deletes, re-imports, or replaces `ocp-cluster`. Recovery off-demo uses existing `09_recover_to_discovery.py`, not `dsx-air`.

## Fallback invocation

```bash
cd scripts && uv run python -m dsx_air demo
```

# Air Ansible Phase 1 Results

Date: 2026-08-18  
Cluster: `ocp-cluster` on DSX Air (sim `c5a70d5b-22cc-42d7-8931-90d0d2f1c45b`)  
OpenShift: **4.19.41** | Nodes: 3× `Ready` (`control-plane,master,worker`)

## Bring-up

| Step | Result | Notes |
|------|--------|-------|
| Start simulation | **PASS** | INACTIVE → ACTIVE (~6 min) |
| Jump host SSH | **PASS** | `ssh -p 22917 ubuntu@worker-8f961120.dsx-air.nvidia.com` |
| Cluster verify | **PASS** | 3 nodes Ready via tunneled `oc` (jump host has no `oc` binary) |
| API tunnel | **PASS** | `ssh -N -L 127.0.0.1:6443:192.168.200.10:6443 -p 22917 ubuntu@worker-8f961120.dsx-air.nvidia.com` |

## MachineConfig pools

| MCP | MACHINECOUNT | Note |
|-----|--------------|------|
| master | 3 | All nodes |
| worker | 0 | Nodes have `worker` **label** but are not in worker MCP — use `node_role: master` for future MachineConfig playbooks |

## Ansible vars

- `vars/ocp_connect_air.yml` in the [Automation-deployment-NVIDIA-Spectrum-X-on-OpenShift](https://gitlab.com/nvidia/spectrum-x/) repo (path varies by checkout)
- Tunneled kubeconfig: `.cache/kubeconfig.ocp-cluster.tunnel` (server `https://127.0.0.1:6443`, `insecure-skip-tls-verify: true`)
- Prerequisite: `pip install --user kubernetes pyyaml` for `kubernetes.core` Ansible modules

## Playbook results

| Playbook | Result | CSV / pods | Notes |
|----------|--------|------------|-------|
| `ocp_connect_test.yml` | **PASS** | ClusterVersion `4.19.41` | |
| `deploy_nfd.yml` | **PASS** (2nd run) | `nfd.4.19.0-202607311600` Succeeded; 6 pods Running | 1st run timed out on CRD wait (~7 min); OLM `ResolutionFailed` on `certified-operators` catalog briefly; self-resolved on retry |
| `deploy_nmstate.yml` | **PASS** | `kubernetes-nmstate-operator.4.19.0-202607311600` Succeeded; handlers/webhook Running | ~5 retries on deployment wait |
| `deploy_sriov.yml` | **PASS** | `sriov-network-operator.v4.19.0-202607311600` Succeeded | `SriovOperatorConfig` created; **0** `SriovNetworkNodePolicy` (expected — no SR-IOV NICs) |

## Deferred (per plan)

- `deploy_nno.yml` — needs reachable NFS + Mellanox PCI
- Rail / GPU / firmware / OVS / LLDP playbooks

## Operational notes

1. Keep SSH tunnel running for all Ansible/`oc` work from laptop.
2. Re-run operator playbooks are idempotent (`ok` on existing resources).
3. OLM catalog resolution can be slow after sim restart; allow ~5–7 min or retry playbooks if CRD/CSV waits fail.
4. `08_verify_cluster.py` fails on jump host without `oc` — use local `oc` + tunnel instead.

## Next steps

- Optional Phase 2: NFS on `oob-mgmt-server` + `deploy_nno.yml` (subscription/PV test only)
- After HOST/CX8 manifests: guest PCI → NicClusterPolicy
- Demo CLI: `uv run dsx-air demo` wraps tunnel + `oc` checks (see [DEMO.md](../DEMO.md))

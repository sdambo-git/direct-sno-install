# Scripts reference

This directory holds every script (and a few standalone YAML manifests) used
by the direct (non-image-based) SNO install documented in `../README.md`.
This file is a quick reference for what each one does and when to run it —
`../README.md` is still the source of truth for the end-to-end walkthrough
and the "why" behind each design decision; this doc just indexes the
scripts themselves.

All scripts expect `AIR_API_KEY` in the environment (or `API_KEY` filled in
at the top of the script) — see the Prerequisites section of `../README.md`.
Run them from inside this `scripts/` directory (`cd scripts && python
01_create_simulation.py`, etc.) since several of them import each other by
module name.

## Normal install flow (in order)

| # | Script | Run when | What it does |
|---|---|---|---|
| — | `upload_discovery_iso.py` | Once, before first import | Uploads the Assisted Installer discovery ISO (downloaded from `console.redhat.com`) to Air as an image named `dsxair-discovery-iso` — must exist in Air *before* `topology.json` is imported, since its `"cdrom"` field references it by name. |
| — | `upload_worker_discovery_iso.py` | Once, before first import (only if `sno-worker-1` is in `topology.json`) | Uploads the worker node's discovery ISO as an image named `worker-discovery-iso`. Run with no arguments to create a **placeholder** (reuses `dsxair-discovery-iso`'s file — a real, working ISO, not a dummy) so you can get the worker up before a dedicated ISO exists. Run with `--replace /path/to/real-worker-discovery.iso` later to swap in the real content **in place** (same image id/name, via the SDK's `clear_upload()` + `upload()`) — no `topology.json` or node changes needed. |
| 1 | `01_create_simulation.py` | Once, after the ISO is uploaded | Imports `../topology.json` and starts the simulation. This is what actually creates `sno-cluster` (and implicitly `oob-mgmt-server` / `oob-mgmt-switch-leaf-1`, since `topology.json` leaves `eth0` on the default OOB network). Also sets up the SSH jump host onto `oob-mgmt-server` and prints the ready-to-use `ssh` command. Refuses to run again if a simulation named `sno-cluster` already exists — delete it first (Air UI) for a truly fresh start. |
| — | *(Assisted Installer console)* | After the node boots | Discovery → validate → install happens in the `console.redhat.com` UI, not via any script here. See `../README.md` Step 4. |
| 5 | `05_detach_discovery_iso.py` | Once, after the install is complete and stable | Detaches the discovery ISO and drops `boot` to `hd`-only. Optional cleanup step — the node keeps working fine with the cdrom still attached, this just tidies up once you're confident you won't need `cdrom` fail-over again. One-way: to get a discovery boot again afterward, use `02_attach_discovery_iso.py` or rebuild the node (see `../README.md`'s boot-order note). |

## Recovery / re-run helpers

| Script | Run when | What it does |
|---|---|---|
| `02_attach_discovery_iso.py` | You need the host to redo discovery (e.g. after **Abort Installation** + **Reset Cluster** in the console) | Re-attaches the `dsxair-discovery-iso` image and sets `boot` to `cdrom`-first, so the host boots into a live discovery agent again. From an earlier iteration of this project (see the caveat below). |
| `03_boot_to_disk.py` | Right when Assisted Installer's progress page shows "Writing image to disk: 100%" / "Rebooting", *before* that reboot happens | Detaches the cdrom and sets `boot` to `hd`-only, so the pending reboot lands on the freshly-installed disk instead of looping back into the discovery ISO. From the same earlier iteration as `02_attach_discovery_iso.py`. |
| `04_create_jump_host_service.py` | Any time after `01_create_simulation.py`, whenever you need the SSH command again | Idempotently creates (or reuses) an SSH Service exposing `oob-mgmt-server`'s port 22, and prints the ready-to-use `ssh` command — your jump host onto `sno-cluster`'s private `192.168.200.x` address. Safe to re-run any number of times. |

**Caveat on `02`/`03`:** `../README.md`'s "boot order stays `[\"hd\",
\"cdrom\"]` — don't toggle it" section explains why these two scripts'
toggle-`boot`-back-and-forth approach is no longer the recommended pattern
now that `topology.json` uses a genuinely blank `hd` (`blank-100g`) +
permanent `["hd", "cdrom"]` order. They're kept because they still work and
are a reasonable fallback, but `node.rebuild()` (resetting the disk back to
blank) is the preferred way to force a fresh discovery boot today.

## Standalone / alternative-path scripts

| Script | What it's for |
|---|---|
| `upload_qcow2_image.py` | A **different** install path than the discovery-ISO flow above: uploads a pre-installed SNO qcow2 disk image to Air as a node's root disk (`image` field), instead of booting from a discovery `cdrom`. No boot-order dance or reboot-loop risk, since the node boots an already-fully-installed disk. Standalone — copy it to wherever the qcow2 file actually lives and run it there. Also how `blank-100g` (the intentionally-empty disk `sno-cluster`/`sno-worker-1` boot from) was created and uploaded. |
| `host-creation.py` | Adds an extra utility/jump-host node (`utility-host`, a `centos9` image by default) directly to the **already-running** `sno-cluster` simulation via `api.nodes.create()`, instead of re-importing the whole topology. Wires onto the same OOB network automatically. Causes a brief outage of the running cluster, since Air requires the simulation to be `INACTIVE` to create a node — this script stops/restarts it around the create call. Useful for ad hoc debugging boxes; for anything that needs to exist from day one (like `sno-worker-1`), define it in `topology.json` instead (see `../README.md`'s "Adding a worker node?" section) since nodes can't be added to a simulation after its first start any other way. |
| `import_topology.py` | An older, simpler version of `01_create_simulation.py` — imports `topology.json` and starts the simulation, but skips the "already exists?" check and doesn't set up the jump host service. Superseded by `01_create_simulation.py`; kept for reference. Prefer `01_create_simulation.py` for normal use. |

## Read-only diagnostic scripts

| Script | What it's for |
|---|---|
| `verify_topology_alignment.py` | Sanity-checks that `topology.json`'s `"cdrom"` image name and `"os"` image name actually resolve to real images in your org's Air catalog, *before* you import. Makes no changes. |
| `diagnose_import.py` | Dumps the raw (unparsed-by-the-SDK) API response for the `sno-cluster` simulation — useful when the SDK's model hides validation error details that the Air UI's History/Timeline panel shows (see `../README.md`'s note on API permission limits for that panel). Makes no changes. |

## Shared helper module

| File | What it's for |
|---|---|
| `air_common.py` | Not a script — a shared helper module every script above imports from. See the "About `scripts/air_common.py`" section in `../README.md` for the full breakdown of what it provides and which scripts use each piece (the short version: it centralizes the stop-simulation → clear-checkpoints → patch → restart dance that Air's API requires for any node edit, plus simulation/node lookup helpers and the jump-host-service helper). |

## Non-script YAML files (not wired to any automation here)

These three files aren't referenced by any script in this directory — they
appear to be carried over from the separate image-based-install (IBI) /
Lifecycle-Agent workflow this project's `README.md` explicitly says it
*doesn't* use ("no seed image, no Lifecycle Agent, ..."). Kept here for
reference/manual `oc apply -f` use if you're cross-referencing that other
workflow, not part of the direct-SNO-install automation:

- `seedgenerator.yaml` — a `SeedGenerator` custom resource (Lifecycle Agent
  API) pointing at a `quay.io/sdambo/airocp` seed image.
- `seedgen.yaml` — a `Secret` holding a `quay.io` pull credential for that
  seed image. **Gitignored** — it contains a real, plaintext-decodable
  credential (base64-wrapped Docker auth config), not a placeholder. Never
  remove it from `.gitignore`.
- `set-core-user-password-machineconfig.yaml` — a `MachineConfig` that sets
  a password hash for the `core` user. **Gitignored** out of caution, since
  it embeds a real password hash rather than a placeholder.

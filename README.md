# Regular (non-image-based) SNO install in NVIDIA DSX Air

This is a standard Single Node OpenShift install using the **Assisted
Installer** discovery ISO from `console.redhat.com` — no seed image, no
Lifecycle Agent, no `MachineConfig` partition tricks, no `cdrom`-swap
sequence. One Air node, one boot, one install.

This is a separate, independent path from the image-based install (IBI)
material in `../seed-cluster/` and the parent kit's root-level files. Use
this if you just want a working SNO cluster in Air, not the seed-image
factory/rapid-fanout workflow.

## Prerequisites

- A Red Hat account with access to
`console.redhat.com/openshift/assisted-installer`, and a pull secret
from `console.redhat.com/openshift/install/pull-secret`.
- An SSH key you want baked into the node.
- An NVIDIA Air / DSX Air account with API access (`pip install nv-air-sdk`).
Air auth is NGC-based: generate a Personal API Key at
`org.ngc.nvidia.com/setup/api-keys` → **+ Generate Personal Key**, making
sure **NVIDIA Air** is checked under **Services Included**. Pass it to the
SDK via `AirApi.with_api_key(api_key="nvapi-...")` (Step 2 below). Your
org's `air` role covers simulation/read image access; if uploading the
ISO in Step 2 403s, you likely also need the `air-image-uploader` role.
- One Air node sized to whatever you want to test against (there's no
documented requirement to match a real bare-metal machine's CPU core
count here — that constraint was specific to the seed-image workflow.
  Match it anyway if you want your lab to behave like your real target
  hardware for other reasons, but it's not a functional requirement now).
  SNO minimums: 8 vCPU / 32 GB RAM / 100 GB disk. `topology.json` uses
  `storage: 100` — check your org's storage budget before going higher
  (`Provided storage amount of 120 GB exceeds the organization's budget of
  100 GB` is the exact error you'll hit at import time if you do; the org
  budget here is capped at 100 GB total).

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
Air's node console.

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
so the ssh command is ready and waiting by the time you get to Step 5 — it's
not a blocker for getting the install itself to complete either way.

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
disk (a genuinely empty, unbootable qcow2 image — see `scripts/upload_qcow2_image.py`
and the `qemu-img create -f qcow2 blank-100g.qcow2 100G` command used to make
it) rather than a real OS image. This combination is intentional and is
meant to be **left alone** through the entire install lifecycle:

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

## Important: the CD-ROM image must exist *before* you import the topology

We hit this for real: importing a topology with `"cdrom": "cdrom-nonbootable"`
failed validation with `Image could not be found: cdrom-nonbootable` — that
placeholder name from the public docs isn't a shared image available to
every org, it doesn't exist in this one. The doc's own rule (*"A CD-ROM must
be attached whenever the node's boot order includes cdrom"*) means the
referenced image has to already exist in your org at **import time**, not
after. So we're reordering the flow: get the real discovery ISO and upload
it to Air *first*, then bake its actual name into `topology.json`, then
import. No placeholder needed, no `cdrom`-swap needed either — Assisted
Installer only ever needs the one ISO.

`topology.json` otherwise reflects the real schema from this org's
`rhel9.json` export rather than the generic public docs — flat
`nic_model`/`cpu_mode`/`secureboot`, `"features": {"uefi": ...}` instead of
`"advanced"`, and explicit per-link `"mac"` when links are used. `"os":
"rhel"` — double-check that's the exact catalog name for your account with
`next(api.images.list(search='rhel'))`.

## Step 1 — Create the cluster in the Assisted Installer console

1. Go to `console.redhat.com/openshift/assisted-installer/clusters` →
  **Create cluster**.
2. Select **Datacenter** → **Single Node OpenShift**, choose your OCP
  version and `x86_64`.
3. Set cluster name and base domain.
4. On the **Static network configuration** step: skip it and let DHCP
   handle it. With the OOB-network topology (see the note above), the node
   gets a real, non-link-local address (`192.168.200.x/24`) automatically —
   you don't know the exact address ahead of time, so there's nothing valid
   to type into a static config here anyway.
5. Download the **Discovery ISO** (minimal image is fine — the node has
  outbound internet access to fetch the rest at boot).

## Step 2 — Upload the ISO to Air *before* importing the topology

Use `scripts/upload_discovery_iso.py`. Open it and fill in:

- `API_KEY` — your NGC Personal API Key from the Prerequisites section
  above (or leave it `None` and `export AIR_API_KEY=...` instead).
- `ISO_PATH` — wherever your browser actually saved the discovery ISO in
  Step 1, usually `~/Downloads/<something>-discovery.iso` named after your
  cluster (check the real filename — it won't literally be
  `discovery_image_sno-cluster.iso`).

Then run it:

```bash
python scripts/upload_discovery_iso.py
```

`IMAGE_NAME` in the script (`dsxair-discovery-iso`) is what the image is
called *inside* Air — unrelated to `ISO_PATH`/the local filename — and it
already matches `"cdrom": "dsxair-discovery-iso"` in `topology.json`. If you
change one, update the other to match before importing.

### Adding a worker node? Decide *now* — it has to happen at creation time, with 2 discovery ISOs

If you want a worker alongside the SNO host (`sno-worker-1` in
`topology.json`), it must be added **before** the simulation is first
imported/started — per Air's own docs, *"You cannot add, remove, or edit
nodes after the simulation starts for the first time."* There's no
"add a node to a running simulation" API; the only way to add one after the
fact is to delete the whole simulation and re-import a `topology.json` that
already includes it (losing whatever state the existing nodes had). So
finalize your full node list, including any workers, before you ever run
Step 3 for the first time.

Each node with `"boot": [..., "cdrom"]` also needs its own `cdrom` image
already uploaded to Air *before* import (same rule as the single-SNO case
above) — so a SNO + 1 worker topology needs **two** discovery ISOs attached,
not one:

- `sno-cluster`'s `cdrom` → `dsxair-discovery-iso`
- `sno-worker-1`'s `cdrom` → `worker-discovery-iso`

Run `scripts/upload_discovery_iso.py` twice — once per image name — editing
`IMAGE_NAME` (and `ISO_PATH`, if you downloaded a separate ISO copy for the
worker from the Assisted Installer console) between runs, so both names
referenced by `topology.json`'s `cdrom` fields actually exist in Air by the
time you get to Step 3. (Assisted Installer's discovery ISO is the same
regardless of eventual node role — control-plane vs. worker is decided
later, in Host discovery — so it's fine to upload the exact same ISO file
twice under the two different Air image names if you don't have a second
download handy.)

## Step 3 — Import the topology and boot the node

Run `scripts/01_create_simulation.py`. It imports `topology.json` (now
that the referenced `cdrom` image actually exists) and starts the
simulation. This also implicitly creates `oob-mgmt-switch-leaf-1` and
`oob-mgmt-server` — you don't define those in `topology.json`; Air
auto-provisions them because the node's `eth0` is left on the default OOB
network (see the OOB note above).

The script also creates (or reuses) an SSH Service exposing
`oob-mgmt-server`'s management port, and prints the ready-to-use `ssh`
command for it — that's your jump host onto `sno-cluster`'s private
`192.168.200.x` address for later (Step 5). If you ever need that command
again without re-running this whole script, use
`scripts/04_create_jump_host_service.py`.

```bash
python scripts/01_create_simulation.py
```

If you need the host to redo discovery later (e.g. after a failed install),
don't try to toggle `boot`/`cdrom` on the running node — see "Important:
boot order stays `[\"hd\", \"cdrom\"]`" above. Rebuild the node instead
(`node.rebuild()`), which resets `hd` back to the blank `blank-100g`
template so the next boot naturally falls through to `cdrom` again.

## Step 4 — Discover, validate, install (in the console)

1. Watch `console.redhat.com/openshift/assisted-installer/clusters/<id>` —
  the node phones home and appears under **Host discovery** within a
   minute or two of booting. With the OOB-network fix, the Active
   NIC/IPv4/MAC columns should now populate (a real `192.168.200.x`
   address) instead of showing blank — note that address down, it's your
   API/Ingress VIP.
2. On the **Networking** step, **Machine network** should now show a real
   subnet option (`192.168.200.0/24`-ish) instead of "No subnets are
   currently available" — select it. Set both **API VIP** and **Ingress
   VIP** to the node's own address from step 1 (SNO doesn't use a separate
   load-balancer IP — the single node serves both directly).
3. If you're using the **Create DNS Records** helper (or your own DNS),
   point `api.<cluster>.<domain>` and `*.apps.<cluster>.<domain>` at that
   same `192.168.200.x` address — **not** `169.254.0.2`; that guidance
   applied to the old outbound-link topology and no longer applies.
4. For SNO, the single host is auto-assigned the control-plane role.
  Wait for all validations (CPU/RAM/disk, network connectivity, NTP, DNS)
   to turn green. If something's red, it'll tell you exactly what's wrong —
   this live feedback is one of the nicer parts of this path vs. hand-built
   Agent-based ISOs.
5. Click **Install cluster**. Progress streams live in the console.
6. When it finishes, download `kubeconfig` and the `kubeadmin` password
  from the console's **Cluster details** page.

## Step 5 — Verify

```bash
export KUBECONFIG=~/Downloads/kubeconfig
oc get nodes
oc get clusterversion
```

This only works directly from your laptop if you can actually route to
`192.168.200.x` — which you generally can't, since it's Air's private OOB
subnet. Options, easiest first:

- SSH into `oob-mgmt-server` using the command `scripts/01_create_simulation.py`
  printed in Step 3 (or re-print it any time with
  `python scripts/04_create_jump_host_service.py`), then run `oc` from
  there instead — it's on the same private network, so it reaches the node
  directly. `scp`/paste your `kubeconfig` over first.
- Or add a local port-forward to that same `ssh` command, e.g.
  `-L 6443:<sno-cluster-ip>:6443`, and point `KUBECONFIG`'s server URL at
  `https://localhost:6443` (get `<sno-cluster-ip>` from Host discovery or the
  Air node console) — lets you run `oc` straight from your laptop instead.
- Or create a **Service** (Services tab → Create a service → type `HTTPS`,
  port `6443`, interface = the node's `eth0`) to expose the API externally
  through a public Air FQDN, and point `KUBECONFIG`'s server URL /
  `api.<cluster>.<domain>`'s DNS record at that FQDN instead of the private
  IP directly.

That's it — this is now a real, independent SNO cluster running in your
Air simulation.

## Scripts reference

See `scripts/SCRIPTS.md` for a full index of every script in `scripts/`
(including the standalone/alternative-path and read-only diagnostic ones
not covered step-by-step above) and what each one does.

## About `scripts/air_common.py`

`scripts/air_common.py` is a shared helper module — a small internal
wrapper around `air_sdk` that every numbered script imports from, so the
same logic isn't copy-pasted across all of them. It's not meant to be run
directly. It exists to encapsulate two non-obvious, empirically-discovered
Air API quirks in exactly one place:

- **Node edits require `INACTIVE`.** Patching a node's `cdrom`/`advanced.boot`
  fields (or creating a new node) is rejected/ignored while the simulation
  is `ACTIVE`. Any script that touches a node has to stop the simulation
  first.
- **Checkpoints block further changes.** Air auto-creates a checkpoint on
  shutdown, and it must reach the `COMPLETE` state (not just exist) before
  the simulation/node can be manipulated again — otherwise you hit
  `"The checkpoint must be in the COMPLETE state."`

Rather than every script re-implementing "stop sim → wait for `INACTIVE` →
wait for checkpoints to clear → make the edit → restart → wait for
`ACTIVE`," they call `stop_simulation_and_clear_checkpoints()` /
`start_simulation()` from here instead.

What it provides, and who uses each piece:

| Function | Purpose | Used by |
|---|---|---|
| `get_api()` (re-exported from `upload_discovery_iso.py`) | Single place the `AIR_API_KEY`/`API_KEY` resolution logic lives, so every script authenticates the same way | All scripts |
| `get_simulation()` | Look up the `sno-cluster` simulation by name | `02`, `03`, `04`, `host-creation.py` |
| `get_node()` | Look up a node by name within a simulation | `02`, `03` |
| `wait_for_sim_state()` | Poll until the simulation reaches a target state (`ACTIVE`/`INACTIVE`) | `01` directly, and internally by the two functions below |
| `stop_simulation_and_clear_checkpoints()` | The stop → clear-checkpoints half of the dance, used before any node patch/create | `02`, `03`, `host-creation.py` |
| `start_simulation()` | Restart the simulation and wait for `ACTIVE` afterward | `02`, `03`, `host-creation.py` |
| `ensure_jump_host_service()` | Idempotently expose `oob-mgmt-server`'s SSH port as an Air Service (reuses an existing one instead of duplicating it) | `01` (sets it up right after import), `04` (re-prints/re-creates it any time later) |
| `jump_host_ssh_command()` | Format the ready-to-use `ssh` command for that service | `01`, `04` |

In short: it's the DRY layer that lets the numbered scripts read like clean,
linear step-by-step procedures while all the fiddly state-machine/timing
logic for talking to Air's API lives in exactly one file.
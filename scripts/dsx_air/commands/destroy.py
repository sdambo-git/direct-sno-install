"""Destroy an Air simulation and/or Assisted Installer cluster from a spec."""
from __future__ import annotations

import sys
from pathlib import Path

from dsx_air._bootstrap import ensure_scripts_path
from dsx_air.spec import LabSpec, apply_to_environ, load_spec, preflight_auth

ensure_scripts_path()

from assisted_common import ai_call, get_client  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402
import air_common  # noqa: E402


def _confirm(message: str, *, force: bool) -> None:
    if force:
        return
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to destroy without --force (not a TTY).")
    answer = input(f"{message} [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise SystemExit("Aborted.")


def destroy_lab(
    spec: LabSpec,
    *,
    do_sim: bool,
    do_cluster: bool,
    force: bool,
) -> None:
    apply_to_environ(spec)
    targets = []
    if do_sim:
        targets.append(f"Air simulation {spec.simulation.name!r}")
    if do_cluster:
        targets.append(f"Assisted cluster {spec.cluster.name!r}")
    if not targets:
        raise SystemExit("Nothing to destroy.")
    _confirm("Delete " + " and ".join(targets) + "?", force=force)

    if do_sim:
        _delete_sim(spec.simulation.name)
    if do_cluster:
        _delete_ai_cluster(spec.cluster.name)


def _delete_sim(name: str) -> None:
    api = get_api()
    matches = [s for s in api.simulations.list(search=name) if s.name == name]
    if not matches:
        print(f"No Air simulation named {name!r}.")
        return
    sim = matches[0]
    print(f"Deleting Air simulation {name!r} (id={sim.id}, state={sim.state!r}) ...")
    if sim.state not in {"INACTIVE", "STOPPED"}:
        try:
            air_common.stop_simulation_and_clear_checkpoints(sim)
        except Exception as exc:  # noqa: BLE001
            print(f"  stop before delete: {exc}")
    sim.delete()
    print(f"Deleted simulation {name!r}.")


def _delete_ai_cluster(name: str) -> None:
    infraenv = f"{name}_infra-env"
    ai = get_client(quiet=True)
    if any(e.get("name") == infraenv for e in ai_call(ai, ai.list_infra_envs)):
        print(f"Deleting infraenv {infraenv!r} ...")
        ai_call(ai, lambda: ai.delete_infra_env(infraenv, force=True))
    else:
        print(f"No infraenv named {infraenv!r}.")
    if any(c.get("name") == name for c in ai_call(ai, ai.list_clusters)):
        print(f"Deleting cluster {name!r} ...")
        ai_call(ai, lambda: ai.delete_cluster(name))
    else:
        print(f"No cluster named {name!r}.")


def run_destroy(
    *,
    spec_path: Path,
    sim: bool = False,
    cluster: bool = False,
    force: bool = False,
) -> int:
    spec = load_spec(spec_path)
    preflight_auth(spec)
    do_sim = sim or not cluster
    do_cluster = cluster or not sim
    if sim and not cluster:
        do_sim, do_cluster = True, False
    if cluster and not sim:
        do_sim, do_cluster = False, True
    destroy_lab(spec, do_sim=do_sim, do_cluster=do_cluster, force=force)
    return 0

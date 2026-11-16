"""OpenShift Web Console via SOCKS + host Chrome.

Cluster names are resolved on the jump host (/etc/hosts), where Chromium
SOCKS5 does DNS — not on the laptop.
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from dsx_air import air_status, preflight, tunnel
from dsx_air._bootstrap import ensure_scripts_path, repo_root
from dsx_air.air_status import AirLookupError
from dsx_air.spec import activate_spec

ensure_scripts_path()
import air_common  # noqa: E402
import env_config  # noqa: E402
from upload_discovery_iso import get_api  # noqa: E402

_LOCK = repo_root() / ".cache" / "dsx-air-console.lock"


def _find_chrome() -> Path | None:
    names = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac.is_file():
        return mac
    return None


def _console_url(cluster_name: str, domain: str) -> str:
    return f"https://console-openshift-console.apps.{cluster_name}.{domain}"


def _kubeadmin_passwords(
    cluster_name: str, *, sim_name: str | None = None
) -> list[tuple[str, str]]:
    """Labeled kubeadmin password candidates, best guess first.

    Seed and IBI target clusters can share ``cluster.name`` (``ocp``): the
    seed's Assisted password is downloaded to ``.cache/kubeadmin-password.*``
    while the IBI config ISO writes the target's password to
    ``ibi-config-iso-workdir/auth/kubeadmin-password``. A simulation name
    containing ``ibi-target``/``nodpu`` prefers the IBI file; anything else
    (seed labs) prefers the ``.cache`` one. ``KUBEADMIN_PASSWORD_FILE``
    always wins. Because the heuristic can guess wrong, run_console
    verifies the candidates against the cluster's OAuth server before
    printing one.
    """
    root = repo_root()
    ibi_entry: tuple[str, str] | None = None
    ibi = root / "ibi-config-iso-workdir" / "auth" / "kubeadmin-password"
    if ibi.is_file():
        text = ibi.read_text().strip()
        if text:
            ibi_entry = ("ibi-config-iso-workdir/auth (IBI target)", text)

    cache_entries: list[tuple[str, str]] = []
    cache = root / ".cache"
    named = [
        cache / "kubeadmin-password",
        cache / f"kubeadmin-password.{cluster_name}",
        cache / f"{cluster_name}-kubeadmin-password",
    ]
    for path in [*named, *sorted(cache.glob("*kubeadmin*"))]:
        if path.is_file():
            text = path.read_text().strip()
            if text and all(pw != text for _, pw in cache_entries):
                cache_entries.append((f".cache/{path.name}", text))

    override_entry: tuple[str, str] | None = None
    override = os.environ.get("KUBEADMIN_PASSWORD_FILE", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            text = path.read_text().strip()
            if text:
                override_entry = (f"KUBEADMIN_PASSWORD_FILE ({path})", text)

    lowered = (sim_name or "").lower()
    ibi_first = "ibi-target" in lowered or "nodpu" in lowered
    ordered: list[tuple[str, str]] = []
    if override_entry:
        ordered.append(override_entry)
    pairs = [ibi_entry, *cache_entries] if ibi_first else [*cache_entries, ibi_entry]
    ordered.extend(entry for entry in pairs if entry is not None)

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for label, pw in ordered:
        if pw not in seen:
            seen.add(pw)
            unique.append((label, pw))
    return unique


def _kubeadmin_password(cluster_name: str, *, sim_name: str | None = None) -> str | None:
    """Primary kubeadmin password candidate (see _kubeadmin_passwords)."""
    candidates = _kubeadmin_passwords(cluster_name, sim_name=sim_name)
    return candidates[0][1] if candidates else None


def _password_works(
    target: tunnel.JumpTarget, oauth_url: str, password: str
) -> bool | None:
    """Ask the cluster's OAuth server whether kubeadmin/password is valid.

    Runs curl on the jump host (which resolves the cluster's route names).
    Returns True/False when the server answered; None when the probe itself
    failed (SSH/curl) — treat that as "do not know", never as invalid.
    """
    remote = (
        "curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "
        + shlex.quote(f"-ukubeadmin:{password}")
        + " "
        + shlex.quote(oauth_url)
    )
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(target.port),
        f"{target.username}@{target.host}",
        remote,
    ]
    try:
        run = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    code = (run.stdout or "").strip()
    if code.startswith("3"):
        return True
    if code in {"401", "403", "500"}:
        return False
    return None


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _chrome_args(
    *,
    binary: Path,
    cluster_name: str,
    domain: str,
    ingress_vip: str,
    api_vip: str,
    url: str,
) -> list[str]:
    profile = repo_root() / ".cache" / "dsx-air-chrome"
    profile.mkdir(parents=True, exist_ok=True)
    rules = (
        f"MAP *.apps.{cluster_name}.{domain} {ingress_vip}, "
        f"MAP api.{cluster_name}.{domain} {api_vip}"
    )
    return [
        str(binary),
        f"--user-data-dir={profile}",
        "--proxy-server=socks5://127.0.0.1:1080",
        f"--host-resolver-rules={rules}",
        "--disable-features=AsyncDns",
        "--dns-over-https-mode=off",
        "--ozone-platform=x11",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def run_console(
    *,
    spec_path: Path | None = None,
    print_only: bool = False,
    wait_recovery: bool = True,
) -> int:
    spec = activate_spec(spec_path)
    if spec is not None:
        cluster_name = spec.cluster.name
        sim_name = spec.simulation.name
    else:
        cluster_name = env_config.cluster_name()
        sim_name = env_config.simulation_name()
    domain = env_config.base_dns_domain()
    api_vip = air_status.api_forward_ip(cluster_name=cluster_name)
    ingress_vip = air_status.ingress_forward_ip(api_forward=api_vip)

    url = _console_url(cluster_name, domain)
    try:
        api = get_api()
        sim = air_common.get_simulation(api, sim_name)
        jump = air_status.jump_host_info(sim=sim)
    except (AirLookupError, SystemExit) as exc:
        print(exc, file=sys.stderr)
        return 1
    target = air_status.jump_target_from_info(jump)
    if target is None or jump.get("ready") != "yes":
        print(
            jump.get("reason") or "Jump host is not ready. Run dsx-air start first.",
            file=sys.stderr,
        )
        return 1
    try:
        service, server = air_common.ensure_jump_host_service(sim)
        air_common.ensure_jump_host_cluster_dns(
            service,
            server,
            cluster_name=cluster_name,
            domain=domain,
            api_vip=api_vip,
            ingress_vip=ingress_vip,
        )
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    # SNO/IBI targets only: refuse to launch Chrome into a node whose
    # workload pods are dead (e.g. expired kubelet cert after a long Air
    # sleep) — SOCKS comes up fine, every console connection is refused.
    # The preflight approves the pending kubelet CSRs and waits for the
    # cluster to self-heal, so Chrome launches only when the console is
    # actually reachable.
    if not env_config.is_multinode():
        result = preflight.run_preflight(
            target,
            api_vip,
            cluster_name=cluster_name,
            wait_recovery=wait_recovery,
            progress=lambda r, e: print(preflight.progress_line(r, e), file=sys.stderr),
        )
        for line in result.messages:
            print(line, file=sys.stderr)
        if result.report.needs_fix:
            return 1

    ssh_cmd = tunnel.build_console_ssh_command(target=target, api_vip=api_vip)
    chrome = _find_chrome()
    print(f"Console: {url}")
    print("Username: kubeadmin")
    candidates = _kubeadmin_passwords(cluster_name, sim_name=sim_name)
    if not candidates:
        print("Password: (no kubeadmin password file found)")
    else:
        # Seed and target password files can both exist locally with the
        # same cluster name; ask the cluster which one is actually valid.
        oauth_url = (
            f"https://oauth-openshift.apps.{cluster_name}.{domain}"
            "/oauth/authorize?response_type=token&client_id=openshift-challenging-client"
        )
        chosen = candidates[0]
        note = "unverified"
        for label, pw in candidates[:3]:
            works = _password_works(target, oauth_url, pw)
            if works is True:
                if (label, pw) != candidates[0]:
                    print(
                        f"# note: heuristic pick was {candidates[0][0]}; "
                        f"verified {label} instead",
                        file=sys.stderr,
                    )
                chosen = (label, pw)
                note = "verified against the cluster's OAuth server"
                break
            if works is None:
                note = "could not verify (OAuth probe failed)"
                break
        print(f"Password: {chosen[1]}   # {chosen[0]} — {note}")

    chrome_line = None
    if chrome is not None:
        chrome_line = " ".join(
            shlex.quote(a)
            for a in _chrome_args(
                binary=chrome,
                cluster_name=cluster_name,
                domain=domain,
                ingress_vip=ingress_vip,
                api_vip=api_vip,
                url=url,
            )
        )

    if chrome is None:
        print(
            "No Chromium/Chrome on PATH (or macOS Google Chrome app). "
            "Install a browser and re-run, or use --print-only.",
            file=sys.stderr,
        )

    if print_only or chrome is None:
        print(ssh_cmd)
        if chrome_line:
            print(chrome_line)
        return 0 if print_only or chrome is not None else 1

    if _LOCK.is_file():
        print(f"Another console session may be running ({_LOCK}).", file=sys.stderr)
        return 1
    _LOCK.parent.mkdir(parents=True, exist_ok=True)
    _LOCK.write_text(str(os.getpid()))

    ssh_proc: subprocess.Popen[str] | None = None
    chrome_proc: subprocess.Popen[str] | None = None
    try:
        ssh_proc = subprocess.Popen(shlex.split(ssh_cmd))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _port_open(1080):
                break
            if ssh_proc.poll() is not None:
                raise SystemExit("SSH SOCKS process exited before port 1080 was listening.")
            time.sleep(0.2)
        else:
            raise SystemExit("Timed out waiting for SOCKS 127.0.0.1:1080.")

        chrome_proc = subprocess.Popen(
            _chrome_args(
                binary=chrome,
                cluster_name=cluster_name,
                domain=domain,
                ingress_vip=ingress_vip,
                api_vip=api_vip,
                url=url,
            )
        )
        print("Ctrl-C to close Console session.")

        def _stop(_signum, _frame) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        while True:
            if chrome_proc.poll() is not None:
                break
            if ssh_proc.poll() is not None:
                print("SSH tunnel exited.", file=sys.stderr)
                break
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        print("\nStopping Console session.")
        return 0
    finally:
        for proc in (chrome_proc, ssh_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if _LOCK.is_file():
            _LOCK.unlink()

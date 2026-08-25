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

from dsx_air import air_status, tunnel
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


def _kubeadmin_password(cluster_name: str) -> str | None:
    cache = repo_root() / ".cache"
    candidates = (
        cache / "kubeadmin-password",
        cache / f"kubeadmin-password.{cluster_name}",
        cache / f"{cluster_name}-kubeadmin-password",
    )
    for path in candidates:
        if path.is_file():
            return path.read_text().strip()
    for path in sorted(cache.glob("*kubeadmin*")):
        if path.is_file():
            text = path.read_text().strip()
            if text:
                return text
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
) -> int:
    if spec_path is not None:
        spec = activate_spec(spec_path)
        assert spec is not None
        cluster_name = spec.cluster.name
        sim_name = spec.simulation.name
    else:
        cluster_name = env_config.cluster_name()
        sim_name = env_config.simulation_name()
    domain = env_config.base_dns_domain()
    ingress_vip = env_config.ingress_vip()
    api_vip = env_config.api_vip()

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

    ssh_cmd = tunnel.build_console_ssh_command(target=target, api_vip=api_vip)
    chrome = _find_chrome()
    password = _kubeadmin_password(cluster_name)
    print(f"Console: {url}")
    print("Username: kubeadmin")
    print(f"Password: {password or '(not found under .cache/)'}")

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

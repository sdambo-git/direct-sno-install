#!/usr/bin/env python3
"""
Install the OpenShift client and kubeconfig on the jump host (oob-mgmt-server).

Downloads `oc` from mirror.openshift.com into `.cache/oc-client/` when
needed (same flow as:

    mkdir -p oc-4.12-test && cd oc-4.12-test
    curl -Ls https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.12.0/openshift-client-linux.tar.gz -o oc.tar.gz
    tar xzf oc.tar.gz oc
    ./oc version --client

Version defaults to 4.12.0; set OC_CLIENT_VERSION or OCP_VERSION to match
the cluster). Copies the binary to `/usr/local/bin/oc` and the kubeconfig
to `~/.kube/config`. Writes `api` / `api-int` / console / oauth names into
`/etc/hosts` (and a dnsmasq wildcard for `*.apps` when dnsmasq is already
running). Does not run `oc get nodes`.

    uv run 08_verify_cluster.py
    KUBECONFIG=.cache/kubeconfig.ocp-cluster uv run 08_verify_cluster.py --local
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from air_common import ensure_jump_host_ready, get_api, get_simulation, jump_host_ssh_command
import env_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OC_CACHE_DIR = _REPO_ROOT / ".cache" / "oc-client"
_OC_MIRROR = (
    "https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp"
)
_REMOTE_TMP_OC = "/tmp/oc"
_REMOTE_OC = "/usr/local/bin/oc"
_REMOTE_KUBECONFIG = ".kube/config"
_REMOTE_APPLY_HOSTS = "/tmp/dsxair-apply-hosts.py"
_HOSTS_BEGIN = "# BEGIN dsxair-openshift"
_HOSTS_END = "# END dsxair-openshift"
_APPS_HOSTS = (
    "console-openshift-console",
    "oauth-openshift",
    "downloads-openshift-console",
    "canary-openshift-ingress-canary",
)


def _kubeconfig_path() -> Path:
    env = os.environ.get("KUBECONFIG")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / ".cache" / f"kubeconfig.{env_config.cluster_name()}"


def _oc_url(version: str) -> str:
    return f"{_OC_MIRROR}/{version}/openshift-client-linux.tar.gz"


def ensure_oc() -> Path:
    """Return a local `oc` binary, downloading it if needed."""
    cached = _OC_CACHE_DIR / "oc"
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached
    on_path = shutil.which("oc")
    if on_path:
        return Path(on_path)

    version = env_config.oc_client_version()
    versions = [version]
    if version != env_config.DEFAULT_OC_CLIENT_VERSION:
        versions.append(env_config.DEFAULT_OC_CLIENT_VERSION)

    _OC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tarball = _OC_CACHE_DIR / "oc.tar.gz"
    last_error = ""
    for ver in versions:
        url = _oc_url(ver)
        print(f"Downloading oc {ver} from {url} ...")
        curl = subprocess.run(
            ["curl", "-fLs", url, "-o", str(tarball)],
            capture_output=True,
            text=True,
            check=False,
        )
        if curl.returncode != 0:
            last_error = (curl.stderr or curl.stdout or "").strip() or f"curl exit {curl.returncode}"
            print(f"  download failed for {ver}: {last_error}")
            continue
        extract = subprocess.run(
            ["tar", "xzf", str(tarball), "oc"],
            cwd=_OC_CACHE_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if extract.returncode != 0 or not cached.is_file():
            last_error = (extract.stderr or extract.stdout or "").strip() or "tar did not produce oc"
            print(f"  extract failed for {ver}: {last_error}")
            continue
        cached.chmod(0o755)
        print(f"oc client ready at {cached}")
        subprocess.run([str(cached), "version", "--client"], check=True)
        return cached

    raise SystemExit(
        f"Could not download oc from mirror.openshift.com ({last_error}). "
        "Install the OpenShift client or set OC_CLIENT_VERSION to a published "
        f"folder under {_OC_MIRROR}/."
    )


def _ssh_opts() -> list[str]:
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def _jump_user(server) -> str:
    return getattr(server.image, "default_username", None) or "ubuntu"


def _jump_host(service, server) -> str:
    return f"{_jump_user(server)}@{service.worker_fqdn}"


def _ssh(service, server, command: str, *, input_text: str | None = None) -> None:
    subprocess.run(
        [
            "ssh",
            *_ssh_opts(),
            "-p",
            str(service.worker_port),
            _jump_host(service, server),
            command,
        ],
        input=input_text,
        text=True,
        check=True,
    )


def _scp(service, server, local: Path, remote: str) -> None:
    subprocess.run(
        [
            "scp",
            *_ssh_opts(),
            "-P",
            str(service.worker_port),
            str(local),
            f"{_jump_host(service, server)}:{remote}",
        ],
        check=True,
    )


def _sudo(service, server, command: str) -> None:
    wrapped = f"sudo -n {command} || sudo -S {command}"
    _ssh(service, server, wrapped, input_text=env_config.jump_host_password() + "\n")


def _cluster_base() -> str:
    return f"{env_config.cluster_name()}.{env_config.base_dns_domain()}"


def _kubeconfig_api_host(kubeconfig: Path) -> str | None:
    for line in kubeconfig.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("server:"):
            host = urlparse(stripped.split(":", 1)[1].strip()).hostname
            return host or None
    return None


def _vip_ips() -> tuple[str, str]:
    api_ip = env_config.api_vip()
    if env_config.is_multinode():
        return api_ip, env_config.ingress_vip()
    return api_ip, api_ip


def _hosts_plan(kubeconfig: Path) -> tuple[str, list[str], str, list[str]]:
    """Return (api_ip, api_names, ingress_ip, ingress_names)."""
    base = _cluster_base()
    apps = f"apps.{base}"
    api_ip, ingress_ip = _vip_ips()
    api_names = [f"api.{base}", f"api-int.{base}"]
    kube_host = _kubeconfig_api_host(kubeconfig)
    if kube_host and kube_host not in api_names and not kube_host.replace(".", "").isdigit():
        api_names.append(kube_host)
    ingress_names = [f"{name}.{apps}" for name in _APPS_HOSTS]
    return api_ip, api_names, ingress_ip, ingress_names


def _apply_hosts_script(kubeconfig: Path) -> str:
    api_ip, api_names, ingress_ip, ingress_names = _hosts_plan(kubeconfig)
    base = _cluster_base()
    apps = f"apps.{base}"
    hosts_lines = [
        _HOSTS_BEGIN,
        f"{api_ip} {' '.join(api_names)}",
    ]
    if ingress_ip == api_ip:
        hosts_lines[1] = f"{api_ip} {' '.join(api_names + ingress_names)}"
    else:
        hosts_lines.append(f"{ingress_ip} {' '.join(ingress_names)}")
    hosts_lines.append(_HOSTS_END)
    dnsmasq_conf = (
        f"address=/{api_names[0]}/{api_ip}\n"
        f"address=/{api_names[1]}/{api_ip}\n"
        f"address=/{apps}/{ingress_ip}\n"
    )
    return f"""#!/usr/bin/env python3
from pathlib import Path
import subprocess

BEGIN = { _HOSTS_BEGIN !r}
END = { _HOSTS_END !r}
BLOCK = { chr(10).join(hosts_lines) + chr(10) !r}
DNSMASQ_CONF = { dnsmasq_conf !r}

text = Path("/etc/hosts").read_text()
out = []
skip = False
for line in text.splitlines(True):
    stripped = line.rstrip("\\n")
    if stripped == BEGIN:
        skip = True
        continue
    if skip and stripped == END:
        skip = False
        continue
    if not skip:
        out.append(line)
Path("/etc/hosts").write_text("".join(out).rstrip() + "\\n\\n" + BLOCK)

dnsmasq_dir = Path("/etc/dnsmasq.d")
if dnsmasq_dir.is_dir():
    running = subprocess.call(["pidof", "dnsmasq"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    if running:
        dnsmasq_dir.joinpath("dsxair-openshift.conf").write_text(DNSMASQ_CONF)
        subprocess.call(["systemctl", "reload", "dnsmasq"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.call(["killall", "-HUP", "dnsmasq"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"""


def _copy_kubeconfig_to_jump_host(service, server, kubeconfig: Path) -> None:
    _ssh(service, server, "mkdir -p ~/.kube && chmod 700 ~/.kube")
    _scp(service, server, kubeconfig, _REMOTE_KUBECONFIG)
    _ssh(service, server, f"chmod 600 ~/{_REMOTE_KUBECONFIG}")


def _install_oc_on_jump_host(service, server, oc: Path) -> None:
    _scp(service, server, oc, _REMOTE_TMP_OC)
    _sudo(
        service,
        server,
        f"bash -c 'install -m 0755 {_REMOTE_TMP_OC} {_REMOTE_OC} && rm -f {_REMOTE_TMP_OC} && test -x {_REMOTE_OC}'",
    )


def _install_cluster_dns_on_jump_host(service, server, kubeconfig: Path) -> None:
    api_ip, api_names, ingress_ip, ingress_names = _hosts_plan(kubeconfig)
    print("Writing cluster DNS names on the jump host:")
    print(f"  {api_ip}  {' '.join(api_names)}")
    print(f"  {ingress_ip}  {' '.join(ingress_names)}")
    script = _apply_hosts_script(kubeconfig)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(script)
        local = Path(handle.name)
    try:
        _scp(service, server, local, _REMOTE_APPLY_HOSTS)
    finally:
        local.unlink(missing_ok=True)
    _sudo(service, server, f"/usr/bin/python3 {_REMOTE_APPLY_HOSTS}")
    _ssh(service, server, f"rm -f {_REMOTE_APPLY_HOSTS}")
    _ssh(service, server, f"getent hosts {api_names[0]} {ingress_names[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run oc get nodes locally (only if the API is reachable from this machine).",
    )
    args = parser.parse_args()

    kubeconfig = _kubeconfig_path()
    if not kubeconfig.is_file():
        raise SystemExit(
            f"Kubeconfig not found: {kubeconfig}. Run 07_install_cluster.py first."
        )

    oc = ensure_oc()

    if args.local:
        env = os.environ.copy()
        env["KUBECONFIG"] = str(kubeconfig)
        subprocess.run([str(oc), "get", "nodes"], env=env, check=True)
        subprocess.run([str(oc), "get", "clusterversion"], env=env, check=True)
        return

    api = get_api()
    sim = get_simulation(api)
    service, server = ensure_jump_host_ready(sim)
    ssh = jump_host_ssh_command(service, server)
    print(f"Copying oc, kubeconfig, and cluster DNS to the jump host via:\n\n    {ssh}\n")
    _install_oc_on_jump_host(service, server, oc)
    _copy_kubeconfig_to_jump_host(service, server, kubeconfig)
    _install_cluster_dns_on_jump_host(service, server, kubeconfig)
    print(
        f"Installed {_REMOTE_OC}, ~/{_REMOTE_KUBECONFIG}, and /etc/hosts entries "
        "on oob-mgmt-server. SSH in and run oc yourself."
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {env_config.describe_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc

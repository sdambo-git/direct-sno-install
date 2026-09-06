from __future__ import annotations

from dataclasses import dataclass

from dsx_air._bootstrap import ensure_scripts_path


@dataclass(frozen=True)
class JumpTarget:
    host: str
    port: int
    username: str

    @property
    def ssh_command(self) -> str:
        return f"ssh -p {self.port} {self.username}@{self.host}"


def build_tunnel_command(*, target: JumpTarget, api_vip: str) -> str:
    """Return ssh -N -L command forwarding local 6443 to API VIP via jump host."""
    return (
        f"ssh -N -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
        f"-L 127.0.0.1:6443:{api_vip}:6443 "
        f"-p {target.port} {target.username}@{target.host}"
    )


def build_console_ssh_command(*, target: JumpTarget, api_vip: str) -> str:
    """SOCKS on 1080 plus API LocalForward 6443 through the jump host."""
    return (
        f"ssh -N -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
        f"-D 127.0.0.1:1080 "
        f"-L 127.0.0.1:6443:{api_vip}:6443 "
        f"-p {target.port} {target.username}@{target.host}"
    )


def api_server_name() -> str:
    """SNI / Host the API VIP expects (api.<cluster>.<domain>)."""
    ensure_scripts_path()
    import env_config  # noqa: E402

    return f"api.{env_config.cluster_name()}.{env_config.base_dns_domain()}"


def api_reachable(*, timeout: float = 45.0, kubeconfig_file: str | None = None) -> tuple[bool, str]:
    """Probe the API the same way ``oc`` does (kubeconfig server URL).

    A raw TLS GET to ``https://127.0.0.1:6443`` often times out even when
    ``oc`` works via ``api.<cluster>.<domain>`` in /etc/hosts.
    """
    from dsx_air import kubeconfig as kcmod
    from dsx_air.oc_checks import raw_version

    if kubeconfig_file:
        result = raw_version(kubeconfig=kubeconfig_file, timeout=timeout)
        if result.ok:
            return True, "yes"
        return False, result.reason or "oc get --raw /version failed"

    ensure_scripts_path()
    import env_config  # noqa: E402

    path, reason = kcmod.require_api(
        cluster_name=env_config.cluster_name(),
        timeout=timeout,
    )
    if path is not None:
        return True, "yes"
    return False, reason

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass


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


def api_reachable(*, timeout: float = 5.0) -> tuple[bool, str]:
    """Probe OpenShift API via local tunnel (TLS verify skipped, like tunneled kubeconfig)."""
    url = "https://127.0.0.1:6443/version"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            if 200 <= resp.status < 300:
                return True, "yes"
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason if hasattr(exc, "reason") else exc)
    except OSError as exc:
        return False, str(exc)

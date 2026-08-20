from __future__ import annotations

import urllib.error
import urllib.request


def build_tunnel_command(*, jump_ssh: str, api_vip: str) -> str:
    """Return ssh -N -L command forwarding local 6443 to API VIP."""
    return (
        f"ssh -N -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
        f"-L 127.0.0.1:6443:{api_vip}:6443 {jump_ssh}"
    )


def api_reachable(*, timeout: float = 5.0) -> tuple[bool, str]:
    """Probe OpenShift API via local tunnel."""
    url = "https://127.0.0.1:6443/version"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, "yes"
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason if hasattr(exc, "reason") else exc)
    except OSError as exc:
        return False, str(exc)

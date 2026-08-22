"""Work out which URL to hand out to phones and tablets.

`hostname -I | awk '{print $1}'` is not good enough on this machine: it has a
docker bridge per project, a k3s/flannel overlay, a VPN tunnel and a handful of
veths — thirteen addresses, twelve of them useless to a phone. Guessing wrong
prints a URL nobody can open, with no hint that anything is wrong.
"""
import re
import subprocess

# Interfaces no phone can ever reach: loopback, container/VM bridges, the k8s
# overlay, and VPN tunnels (a tunnel address is routable only inside the VPN).
VIRTUAL_PREFIXES = (
    "lo", "docker", "br-", "veth", "virbr", "flannel", "cni",
    "tun", "tap", "wg", "ava-br", "kube", "cali", "zt",
)

_ROUTE_SRC = re.compile(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)")
_ADDR_LINE = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/")


def is_virtual(interface):
    return interface.startswith(VIRTUAL_PREFIXES)


def primary_ip(route_output):
    """The source address the kernel picks for off-LAN traffic, or None.

    This is the address on the interface that actually carries the default
    route — the one a phone on the same WiFi will be able to reach.
    """
    match = _ROUTE_SRC.search(route_output or "")
    return match.group(1) if match else None


def physical_ips(addr_output):
    """[(interface, ipv4)] for real network cards, in `ip` ordering."""
    found = []
    for line in (addr_output or "").splitlines():
        match = _ADDR_LINE.match(line.strip())
        if match and not is_virtual(match.group(1)):
            found.append((match.group(1), match.group(2)))
    return found


def service_urls(port, route_output, addr_output):
    """[(interface, url)] worth printing — default-route interface first."""
    candidates = physical_ips(addr_output)
    chosen = primary_ip(route_output)
    candidates.sort(key=lambda pair: pair[1] != chosen)
    seen, urls = set(), []
    for interface, ip in candidates:
        if ip in seen:
            continue
        seen.add(ip)
        urls.append((interface, f"http://{ip}:{port}"))
    return urls


def current_ssid(nmcli_output):
    """The WiFi network this machine is on, from `nmcli -t -f active,ssid dev wifi`."""
    for line in (nmcli_output or "").splitlines():
        if line.startswith("yes:"):
            return line.split(":", 1)[1].strip() or None
    return None


def startup_banner(port, route_output, addr_output, nmcli_output="", control_token=""):
    """The block start.sh prints. Names the WiFi, because the usual failure is
    a phone sitting on a different network and no way to tell."""
    urls = service_urls(port, route_output, addr_output)
    if not urls:
        return ("\u26a0\ufe0f  no network address found — this laptop is not on any "
                "WiFi or LAN,\n    so no phone or tablet can reach it.")

    _, primary = urls[0]
    lines = [
        f"\U0001f4fa Projector/tablet:  {primary}/display",
        f"\U0001f4f1 Personal phones:   {primary}/view",
        f"\U0001f3a4 Microphone page:   {primary}/mic",
    ]
    if control_token:
        lines.append(f"\U0001f39b\ufe0f  Operator control:  {primary}/control/{control_token}")
    lines.append("")
    ssid = current_ssid(nmcli_output)
    if ssid:
        lines.append(f"   Phones MUST be on this WiFi:  {ssid}")
    else:
        lines.append("   Phones must be on the same network as this laptop.")

    if len(urls) > 1:
        others = ", ".join(f"{url} ({iface})" for iface, url in urls[1:])
        lines += ["", f"   Other addresses on this machine: {others}",
                  "   Use one of those only if the first does not work."]
    return "\n".join(lines)


def _run(*command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def local_urls(port):
    """service_urls() against this machine's live network configuration."""
    return service_urls(port, _run("ip", "route", "get", "1.1.1.1"),
                        _run("ip", "-o", "-4", "addr", "show"))


def local_banner(port, control_token=""):
    return startup_banner(
        port,
        _run("ip", "route", "get", "1.1.1.1"),
        _run("ip", "-o", "-4", "addr", "show"),
        _run("nmcli", "-t", "-f", "active,ssid", "dev", "wifi"),
        control_token,
    )


if __name__ == "__main__":
    import os
    import sys
    from config import PORT          # single source of truth for the port
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    print(local_banner(port, os.environ.get("CONTROL_TOKEN", "")))

"""Network trust: client IPs, HTTPS detection, origins and the bind guard.

``X-Forwarded-*`` headers are attacker-controlled unless the request actually
came from a proxy you listed in ``TRUSTED_PROXIES``. Everything here therefore
takes the peer address and only consults forwarded headers for trusted peers.

The helpers are plain functions of strings and parsed networks so they can be
unit tested without a running server.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

LOOPBACK_NAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class BindRefused(RuntimeError):
    """The requested listen address is not allowed yet."""


class ProxyConfigError(ValueError):
    """TRUSTED_PROXIES contains something that is not a network."""


def parse_networks(values: Any) -> Tuple[IPNetwork, ...]:
    """Parse comma-separated CIDRs (bare addresses become /32 or /128)."""
    if not values:
        return ()
    if isinstance(values, str):
        raw: Iterable[str] = values.replace("\n", ",").split(",")
    else:
        raw = [str(value) for value in values]

    networks: List[IPNetwork] = []
    for item in raw:
        text = item.strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise ProxyConfigError(f"'{text}' is not an IP address or CIDR block.") from exc
    return tuple(networks)


def is_loopback_host(host: str) -> bool:
    """True for loopback names and addresses, including an empty bind host."""
    text = (host or "").strip().strip("[]").lower()
    if not text:
        return False
    if text in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def peer_is_trusted(peer: str, networks: Sequence[IPNetwork]) -> bool:
    if not networks:
        return False
    try:
        address = ipaddress.ip_address((peer or "").strip().strip("[]"))
    except ValueError:
        return False
    return any(address in network for network in networks)


def forwarded_hops(xff: Optional[str]) -> List[str]:
    return [hop.strip() for hop in (xff or "").split(",") if hop.strip()]


def resolve_client_ip(
    peer: str,
    xff: Optional[str],
    trusted: Sequence[IPNetwork],
) -> str:
    """The client address, walking the forwarded chain from the right.

    Untrusted peers cannot influence their reported address at all. For a
    trusted proxy, the right-most hop that is not itself a trusted proxy is the
    closest thing to the real client.
    """
    fallback = (peer or "unknown")[:64]
    hops = forwarded_hops(xff)
    if not hops or not peer_is_trusted(peer, trusted):
        return fallback

    for hop in reversed(hops):
        if peer_is_trusted(hop, trusted):
            continue
        return hop[:64]
    return hops[0][:64]


def resolve_secure(
    scheme: str,
    forwarded_proto: Optional[str],
    peer: str,
    trusted: Sequence[IPNetwork],
) -> bool:
    """Whether the browser's connection is HTTPS, trusting proxies only."""
    if peer_is_trusted(peer, trusted) and forwarded_proto:
        first = forwarded_proto.split(",")[0].strip().lower()
        return first == "https"
    return (scheme or "").strip().lower() == "https"


def public_origin(public_base_url: Optional[str]) -> str:
    """``https://agent.example.com`` from PUBLIC_BASE_URL, or ``""``."""
    text = (public_base_url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower().rstrip("/")


def origin_allowed(
    origin: Optional[str],
    host: Optional[str],
    public_base_url: Optional[str] = "",
) -> bool:
    """Same-origin check that also accepts the configured public origin."""
    if not origin:
        return True
    if not host:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    presented = f"{parsed.scheme}://{parsed.netloc}".lower().rstrip("/")
    if presented == f"http://{host}".lower().rstrip("/"):
        return True
    if presented == f"https://{host}".lower().rstrip("/"):
        return True
    base = public_origin(public_base_url)
    return bool(base) and presented == base


def assert_bind_allowed(
    host: str,
    *,
    owner_exists: bool,
    allow_remote_bind: bool,
) -> None:
    """Refuse a non-loopback bind until an owner exists and it was requested.

    Two independent conditions, so neither a forgotten owner nor a stale
    ``ALLOW_REMOTE_BIND`` alone can expose an unowned server.
    """
    if is_loopback_host(host):
        return
    if not owner_exists:
        raise BindRefused(
            f"Refusing to bind {host}: this installation has no owner account yet.\n"
            "Create one on the server first:\n"
            "    python -m server.cli create-owner\n"
            "or start on 127.0.0.1, create the owner, then expose it."
        )
    if not allow_remote_bind:
        raise BindRefused(
            f"Refusing to bind {host}: non-loopback listeners must be explicit.\n"
            "Set ALLOW_REMOTE_BIND=true in the environment (and put HTTPS in front "
            "of the server) if that is really what you want."
        )


__all__ = [
    "BindRefused",
    "LOOPBACK_NAMES",
    "ProxyConfigError",
    "assert_bind_allowed",
    "forwarded_hops",
    "is_loopback_host",
    "origin_allowed",
    "parse_networks",
    "peer_is_trusted",
    "public_origin",
    "resolve_client_ip",
    "resolve_secure",
]

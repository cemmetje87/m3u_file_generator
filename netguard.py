#!/usr/bin/env python3
"""URL safety validation to prevent server-side request forgery (SSRF).

Used by the web API (main.py) before scraping and by the M3U downloader
(downloader.py) before fetching playlist URLs. Rejects non-HTTP schemes and
any host resolving to a private, loopback, link-local (e.g. cloud metadata
169.254.169.254), reserved, or multicast address.

Note: DNS is resolved once here and again by the HTTP client, so a determined
attacker with DNS rebinding could theoretically race this check. For a local
hobby tool behind an API key this residual risk is accepted.
"""
import ipaddress
import socket
from urllib.parse import urlparse

# Hostnames that should never be fetched regardless of DNS resolution.
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain"}


def is_public_http_url(url: str) -> tuple[bool, str]:
    """Check that a URL is safe for the server to fetch.

    Returns (is_safe, reason). reason is "ok" when safe, otherwise a
    human-readable explanation of why the URL was rejected.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "unparseable URL"

    if parsed.scheme not in ("http", "https"):
        return False, f"scheme '{parsed.scheme or '(none)'}' is not allowed (http/https only)"

    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    if host.lower() in BLOCKED_HOSTNAMES:
        return False, f"hostname '{host}' is blocked"

    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, f"DNS resolution failed for '{host}'"

    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"unparseable resolved address '{ip_str}'"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"host '{host}' resolves to non-public address {ip}"

    return True, "ok"

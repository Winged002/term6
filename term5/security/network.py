from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class NetworkGuard:
    def __init__(self, allowed_hosts: set[str] | None = None) -> None:
        self.allowed_hosts = {h.lower() for h in (allowed_hosts or set())}

    @staticmethod
    def _unsafe_ip(ip: str) -> bool:
        obj = ipaddress.ip_address(ip)
        return bool(
            obj.is_private or obj.is_loopback or obj.is_link_local or
            obj.is_multicast or obj.is_reserved or obj.is_unspecified
        )

    def validate_url(self, url: str, *, require_allowlist: bool = False) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise PermissionError("Only http/https URLs are permitted")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise PermissionError("URL has no hostname")
        if host in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
            raise PermissionError("Loopback/metadata host blocked")
        if require_allowlist and host not in self.allowed_hosts:
            raise PermissionError(f"Host is not allowlisted: {host}")
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise PermissionError(f"Host did not resolve: {host}") from exc
        for info in infos:
            ip = info[4][0]
            if self._unsafe_ip(ip):
                raise PermissionError(f"Unsafe network target blocked: {host} -> {ip}")
        return host

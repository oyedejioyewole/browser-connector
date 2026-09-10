"""
Proxy pool management for Camoufox Connector.

Lets the connector be started with *multiple* proxies instead of a single one.
Each browser instance is assigned a proxy from the pool; a failing proxy can be
blacklisted and the instance relaunched with a different one.

This module is intentionally free of any I/O or browser dependencies so its
selection logic can be unit-tested in isolation.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit


def mask_proxy_url(url: Optional[str]) -> Optional[str]:
    """Redact credentials from a proxy URL for safe display in API responses.

    ``http://user:pass@host:8080`` -> ``http://***@host:8080``. Returns the input
    unchanged if it cannot be parsed, and ``None`` for ``None``.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            netloc = f"***@{host}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return url
    except Exception:
        return url


class ProxyPool:
    """Assigns proxies from a list to browser instances, with blacklisting.

    Assignment is deterministic (round-robin by instance index) so a given pool
    configuration produces a stable, reproducible mapping. ``rotate`` picks a
    different, non-blacklisted proxy that is not already assigned to another live
    instance -- used when an instance is relaunched after a proxy failure.
    """

    def __init__(self, proxies: Optional[list[str]] = None):
        # Keep the first copy of each non-empty proxy.
        seen: set[str] = set()
        self._proxies: list[str] = []
        for p in proxies or []:
            p = (p or "").strip()
            if p and p not in seen:
                seen.add(p)
                self._proxies.append(p)
        self._blacklist: set[str] = set()
        self._assigned: dict[int, Optional[str]] = {}

    def __bool__(self) -> bool:
        return bool(self._proxies)

    @property
    def size(self) -> int:
        return len(self._proxies)

    @property
    def proxies(self) -> list[str]:
        return list(self._proxies)

    def available(self) -> list[str]:
        """Proxies that are not blacklisted."""
        return [p for p in self._proxies if p not in self._blacklist]

    def assigned(self, index: int) -> Optional[str]:
        """The proxy currently assigned to an instance index (or None)."""
        return self._assigned.get(index)

    def assign(self, index: int) -> Optional[str]:
        """Assign a proxy to an instance at first launch (deterministic).

        Returns ``None`` when the pool is empty (single/no-proxy mode). If every
        proxy has been blacklisted, falls back to the full list rather than
        starving the instance of a proxy.
        """
        if not self._proxies:
            self._assigned[index] = None
            return None
        pool = self.available() or self._proxies
        proxy = pool[index % len(pool)]
        self._assigned[index] = proxy
        return proxy

    def blacklist(self, proxy: Optional[str]) -> None:
        """Mark a proxy as bad so it is skipped by ``available``/``rotate``."""
        if proxy:
            self._blacklist.add(proxy)

    def rotate(self, index: int, blacklist_current: bool = False) -> Optional[str]:
        """Reassign an instance to a different proxy (used on relaunch).

        Prefers a non-blacklisted proxy that no other instance is currently
        using, and avoids handing back the same proxy when an alternative exists.
        Optionally blacklists the instance's current proxy first.
        """
        if not self._proxies:
            self._assigned[index] = None
            return None

        current = self._assigned.get(index)
        if blacklist_current and current:
            self.blacklist(current)

        in_use = {p for i, p in self._assigned.items() if i != index and p}
        available = self.available() or self._proxies

        # Prefer proxies not in use by another instance.
        candidates = [p for p in available if p not in in_use] or available
        # Prefer something other than the current proxy.
        preferred = [p for p in candidates if p != current] or candidates

        proxy = preferred[0] if preferred else None
        self._assigned[index] = proxy
        return proxy

    def to_dict(self) -> dict:
        """Summary for the /stats endpoint (credentials masked)."""
        return {
            "total": self.size,
            "blacklisted": [mask_proxy_url(p) for p in sorted(self._blacklist)],
            "assignments": {
                str(idx): mask_proxy_url(proxy) for idx, proxy in sorted(self._assigned.items())
            },
        }

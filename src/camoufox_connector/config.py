"""
Configuration handling for Camoufox Connector.

Supports configuration via:
- Command line arguments
- Environment variables
- JSON configuration files
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_PROXY_SCHEMES = ("http://", "https://", "socks5://")


# Reject characters that cannot appear in a valid proxy URL.
_PROXY_FORBIDDEN = re.compile(r"""[\s'"\\`\x00-\x1f]""")


def _validate_proxy_url(v: str) -> str:
    """Validate a single proxy URL's scheme + shape, returning it unchanged."""
    if not v.startswith(_PROXY_SCHEMES):
        raise ValueError(f"Proxy must start with one of {', '.join(_PROXY_SCHEMES)}: {v!r}")
    if _PROXY_FORBIDDEN.search(v):
        raise ValueError(
            f"Proxy URL contains forbidden characters (whitespace/quotes/control): {v!r}"
        )
    return v


class ServerMode(str, Enum):
    """Operating mode for the connector server."""

    SINGLE = "single"
    POOL = "pool"


class Settings(BaseSettings):
    """
    Configuration settings for Camoufox Connector.

    Settings can be configured via environment variables (prefixed with CAMOUFOX_)
    or directly passed as arguments.
    """

    model_config = SettingsConfigDict(
        env_prefix="CAMOUFOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server mode
    mode: ServerMode = Field(
        default=ServerMode.SINGLE,
        description="Operating mode: 'single' for one browser, 'pool' for multiple",
    )

    # Pool configuration
    pool_size: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Number of browser instances in pool mode",
    )

    # Network configuration
    api_port: int = Field(
        default=8080,
        ge=1024,
        le=65535,
        description="HTTP API port for health checks and management",
    )

    ws_port_start: int = Field(
        default=9222,
        ge=1024,
        le=65500,
        description="Starting port for browser WebSocket endpoints",
    )

    api_host: str = Field(
        default="0.0.0.0",
        description="Host to bind the HTTP API to",
    )

    geoip: bool = Field(
        default=True,
        description="Enable GeoIP-based locale/timezone spoofing",
    )

    humanize: bool = Field(
        default=True,
        description="Enable humanization features",
    )

    block_images: bool = Field(
        default=False,
        description="Block image loading for faster page loads",
    )

    # Proxy configuration
    proxy: Optional[str] = Field(
        default=None,
        description="Single proxy URL (http://user:pass@host:port). Kept for "
        "backward compatibility; prefer 'proxies' for a rotating pool.",
    )

    proxies: Optional[str] = Field(
        default=None,
        description="One or more proxy URLs for a rotating pool, separated by "
        "commas or newlines (or given as a JSON list in a config file). Each "
        "browser instance is assigned one proxy from the pool. Takes precedence "
        "over 'proxy' when both are set.",
    )

    # Lease settings for the /acquire and /release API.
    max_concurrency_per_instance: int = Field(
        default=1,
        ge=1,
        le=1000,
        description="Maximum simultaneous /acquire leases a single browser "
        "instance may hold. Only affects the priority /acquire API; /next is "
        "unaffected.",
    )

    preemption: bool = Field(
        default=False,
        description="Allow a higher-priority /acquire request to preempt a "
        "lower-priority active lease when the pool is at capacity.",
    )

    preempt_grace: float = Field(
        default=10.0,
        ge=0.0,
        description="Seconds a preempted lease may keep running before its "
        "browser is reclaimed. The holder can release voluntarily within this "
        "window.",
    )

    restart_on_preempt: bool = Field(
        default=True,
        description="When a preempted lease is force-reclaimed after the grace "
        "period, restart the browser instance so the preempting request gets a "
        "clean session (and the preempted client is cleanly disconnected).",
    )

    acquire_timeout: float = Field(
        default=30.0,
        ge=0.0,
        description="Default seconds an /acquire request waits for capacity "
        "before returning 503. A per-request ?timeout= overrides this.",
    )

    max_waiters: int = Field(
        default=1000,
        ge=1,
        description="Maximum number of queued /acquire requests. Beyond this the "
        "server returns 503 immediately, bounding memory under load.",
    )

    # Debug settings
    debug: bool = Field(
        default=False,
        description="Enable debug logging",
    )

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, v: Optional[str]) -> Optional[str]:
        """Validate the single proxy URL format."""
        if v is None or v == "":
            return None
        return _validate_proxy_url(v)

    @field_validator("proxies", mode="before")
    @classmethod
    def coerce_proxies(cls, v):
        """Accept a list (from JSON config) or a comma/newline string (from env)."""
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            joined = ",".join(str(x).strip() for x in v if str(x).strip())
            return joined or None
        s = str(v).strip()
        return s or None

    @property
    def proxy_list(self) -> list[str]:
        """The effective list of proxies (from 'proxies', else the single 'proxy')."""
        result: list[str] = []
        seen: set[str] = set()
        if self.proxies:
            for part in re.split(r"[,\n]", self.proxies):
                p = part.strip()
                if p and p not in seen:
                    seen.add(p)
                    result.append(p)
        if not result and self.proxy:
            result = [self.proxy]
        return result

    @model_validator(mode="after")
    def validate_proxy_list(self) -> Settings:
        """Validate every proxy in the pool (scheme check)."""
        for p in self.proxy_list:
            _validate_proxy_url(p)
        return self

    @model_validator(mode="after")
    def validate_geoip_requires_proxy(self) -> Settings:
        """Warn and disable geoip if no proxy (single or pool) is configured."""
        if self.geoip and not self.proxy_list:
            logger.warning(
                "geoip=True requires a proxy to be configured. "
                "Automatically disabling geoip. Set a proxy or use --no-geoip to silence this warning."
            )
            self.geoip = False
        return self

    @classmethod
    def from_json(cls, path: str | Path) -> Settings:
        """Load settings from a JSON configuration file."""
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    @classmethod
    def from_cli_args(cls, args) -> Settings:
        """Create settings from parsed CLI arguments."""
        # Convert argparse namespace to dict, filtering None values
        data = {k: v for k, v in vars(args).items() if v is not None}

        # Handle config file if specified
        if "config" in data and data["config"]:
            config_path = data.pop("config")
            base_settings = cls.from_json(config_path)
            # Merge CLI args on top of config file
            return base_settings.model_copy(update=data)

        return cls(**data)

    def get_ws_port(self, index: int = 0) -> int:
        """Get WebSocket port for a given browser instance index."""
        return self.ws_port_start + index

    def to_camoufox_kwargs(self, proxy: Optional[str] = None) -> dict:
        """Convert settings to kwargs for camoufox launch_server.

        ``proxy`` is the per-instance proxy assigned from the pool. When it is
        None the instance runs direct (no proxy) and geoip is disabled for it,
        since geoip has no meaning without an exit IP to derive location from.
        """
        kwargs = {
            # Xvfb provides the display, so the remote server must use headful mode.
            "headless": False,
            "geoip": self.geoip and bool(proxy),
            "humanize": self.humanize,
            "block_images": self.block_images,
        }

        if proxy:
            kwargs["proxy"] = proxy

        return kwargs

"""
Camoufox Connector - WebSocket Bridge for Multi-Language Playwright Access

Connect to Camoufox anti-detect browser from any programming language
via Playwright's remote protocol.
"""

__version__ = "1.0.3"
__author__ = "Scrappey"

from .config import Settings
from .health import create_health_app
from .pool import BrowserInstance, BrowserPool, Lease
from .proxy import ProxyPool, mask_proxy_url
from .server import main

__all__ = [
    "Settings",
    "BrowserPool",
    "BrowserInstance",
    "Lease",
    "ProxyPool",
    "mask_proxy_url",
    "create_health_app",
    "main",
    "__version__",
]

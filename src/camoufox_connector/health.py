"""
HTTP Health Check API for Camoufox Connector.

Provides endpoints for health monitoring and browser pool management.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

if TYPE_CHECKING:
    from .pool import BrowserPool

logger = logging.getLogger(__name__)

# Limit priority values to a practical range.
_PRIORITY_LIMIT = 1_000_000_000


def create_health_app(pool: BrowserPool) -> Starlette:
    """
    Create a Starlette application for health checks and management.

    Args:
        pool: Browser pool instance to monitor

    Returns:
        Starlette application instance
    """

    async def health(request: Request) -> Response:
        """
        Health check endpoint.

        Returns 200 if at least one browser is healthy, 503 otherwise.
        """
        health_status = await pool.health_check()

        status_code = 200 if health_status["healthy"] else 503

        return JSONResponse(
            {
                "status": "healthy" if health_status["healthy"] else "unhealthy",
                "mode": pool.settings.mode.value,
                "instances": health_status["instances"],
            },
            status_code=status_code,
        )

    async def endpoints(request: Request) -> Response:
        """
        Get available WebSocket endpoints.

        Returns a list of all healthy browser endpoints.
        """
        all_endpoints = pool.get_all_endpoints()

        return JSONResponse(
            {
                "endpoints": all_endpoints,
                "count": len(all_endpoints),
            }
        )

    async def next_endpoint(request: Request) -> Response:
        """
        Get the next available endpoint using round-robin.

        This is the primary endpoint for clients to get a browser.
        """
        endpoint = await pool.get_next_endpoint()

        if endpoint is None:
            return JSONResponse(
                {"error": "No healthy browser instances available"},
                status_code=503,
            )

        return JSONResponse(
            {
                "endpoint": endpoint,
            }
        )

    def _parse_float(request: Request, name: str):
        raw = request.query_params.get(name)
        if raw is None or raw == "":
            return None
        v = float(raw)
        if not math.isfinite(v) or v < 0:
            # Reject values that cannot be used as a timeout.
            raise ValueError(f"{name} must be a finite, non-negative number")
        return v

    async def acquire(request: Request) -> Response:
        """
        Acquire a priority-aware lease on a browser instance.

        Query params:
          priority (int, default 0) - higher wins when the pool is at capacity
          timeout  (float seconds)  - how long to wait for capacity; defaults to
                                       the server's acquire_timeout; 0 = no wait

        Returns 200 with {endpoint, lease_id, ...} on success, 503 on timeout.
        Release the browser with POST /release/{lease_id} when done.
        """
        try:
            priority = int(request.query_params.get("priority", 0))
            timeout = _parse_float(request, "timeout")
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "priority must be an int and timeout a finite, non-negative number"},
                status_code=400,
            )

        if abs(priority) > _PRIORITY_LIMIT:
            return JSONResponse(
                {"error": f"priority magnitude must be <= {_PRIORITY_LIMIT}"},
                status_code=400,
            )

        lease = await pool.acquire(priority=priority, timeout=timeout)

        if lease is None:
            return JSONResponse(
                {"error": "No browser capacity available (timed out waiting)"},
                status_code=503,
            )

        return JSONResponse(
            {
                "endpoint": lease.endpoint,
                "lease_id": lease.lease_id,
                "instance": lease.instance_index,
                "priority": lease.priority,
            }
        )

    async def release_lease(request: Request) -> Response:
        """
        Release a previously acquired lease. POST /release/{lease_id}
        """
        lease_id = request.path_params.get("lease_id", "")
        result = await pool.release(lease_id)

        if not result["released"]:
            return JSONResponse(
                {"error": "Unknown or already-released lease", **result},
                status_code=404,
            )

        return JSONResponse({"status": "released", **result})

    async def lease_status(request: Request) -> Response:
        """
        Inspect a lease. GET /lease/{lease_id}

        The `preempted` flag tells a well-behaved high-value holder it has been
        asked to yield (release soon) before the grace period forcibly reclaims
        the browser.
        """
        lease_id = request.path_params.get("lease_id", "")
        info = pool.get_lease(lease_id)

        if info is None:
            return JSONResponse(
                {"error": "Unknown or released lease", "active": False},
                status_code=404,
            )

        return JSONResponse(info)

    async def stats(request: Request) -> Response:
        """
        Get detailed pool statistics.

        Returns connection counts, uptime, and instance details.
        """
        return JSONResponse(pool.get_stats())

    def _parse_bool(request: Request, name: str) -> bool:
        return request.query_params.get(name, "").lower() in ("1", "true", "yes", "on")

    async def restart_instance(request: Request) -> Response:
        """
        Restart a specific browser instance.

        POST /restart/{index}
          ?rotate_proxy=true  - relaunch with a different proxy from the pool
          ?blacklist=true     - also blacklist the instance's current proxy
        """
        try:
            index = int(request.path_params["index"])
        except (KeyError, ValueError):
            return JSONResponse(
                {"error": "Invalid instance index"},
                status_code=400,
            )

        rotate_proxy = _parse_bool(request, "rotate_proxy")
        blacklist = _parse_bool(request, "blacklist")

        success = await pool.restart_instance(
            index, rotate_proxy=rotate_proxy or blacklist, blacklist_proxy=blacklist
        )

        if success:
            return JSONResponse(
                {
                    "status": "restarted",
                    "index": index,
                    "proxy_rotated": rotate_proxy or blacklist,
                }
            )
        else:
            return JSONResponse(
                {"error": f"Failed to restart instance {index}"},
                status_code=500,
            )

    async def info(request: Request) -> Response:
        """
        Get server information and configuration.
        """
        from . import __version__

        proxy_count = len(pool.settings.proxy_list)
        return JSONResponse(
            {
                "name": "camoufox-connector",
                "version": __version__,
                "mode": pool.settings.mode.value,
                "pool_size": len(pool.instances),
                "config": {
                    "headless": True,
                    "geoip": pool.settings.geoip,
                    "humanize": pool.settings.humanize,
                    "block_images": pool.settings.block_images,
                    "proxy": "configured" if proxy_count else None,
                    "proxy_count": proxy_count,
                    "max_concurrency_per_instance": pool.settings.max_concurrency_per_instance,
                    "preemption": pool.settings.preemption,
                },
            }
        )

    routes = [
        Route("/", info, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/endpoints", endpoints, methods=["GET"]),
        Route("/next", next_endpoint, methods=["GET"]),
        Route("/acquire", acquire, methods=["GET", "POST"]),
        Route("/release/{lease_id}", release_lease, methods=["POST"]),
        Route("/lease/{lease_id}", lease_status, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
        Route("/restart/{index:int}", restart_instance, methods=["POST"]),
    ]

    app = Starlette(
        debug=pool.settings.debug,
        routes=routes,
    )

    return app


async def run_health_server(pool: BrowserPool) -> None:
    """
    Run the health check HTTP server.

    Args:
        pool: Browser pool instance to monitor
    """
    import uvicorn

    app = create_health_app(pool)

    config = uvicorn.Config(
        app,
        host=pool.settings.api_host,
        port=pool.settings.api_port,
        log_level="info" if pool.settings.debug else "warning",
        access_log=pool.settings.debug,
    )

    server = uvicorn.Server(config)
    await server.serve()

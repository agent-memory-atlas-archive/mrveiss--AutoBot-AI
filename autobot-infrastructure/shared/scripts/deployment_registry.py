# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One ServiceRegistry for the infrastructure deployment scripts (#18132).

`ServiceStatus` is an ALIAS of the canonical `autobot_shared.status_enums.HealthStatus`,
not a new enum: #6973 forbids a fresh `*Status(Enum)` outside the canonical module, and
the three members the scripts used (HEALTHY/UNHEALTHY/UNKNOWN) are an exact subset of
HealthStatus by NAME AND VALUE, so the alias changes no serialized value.

`ServiceRegistry` was defined six times across `autobot-infrastructure/shared/scripts`,
with six distinct bodies: a copied class that each script then trimmed to what it needed
(9, 8, 8, 4, 3 and 2 methods). The method bodies agreed, which is why a body-level census
reported them as identical duplication -- but the classes were subsets of one another, so
the remediation is a union, not a move.

This is that union: the richest copy's nine methods plus `update_service_url`, which
existed only in `zero_downtime_deploy.py`.

It lives beside the scripts rather than in `autobot_shared/` because these are
standalone scripts that reach siblings through the `_SCRIPTS_DIR`-on-sys.path pattern
they already use for `backup_manager` (#14518). This module reaches `autobot_shared`
for the status alias only through the repo-root bootstrap `deployment_rollback.py`
uses (#11761). The port/health table still MIRRORS
`autobot_shared/ssot_config.py` by hand, as the originals' comments record -- that
mirror is a separate fork and is not resolved here.

Two divergences are preserved deliberately rather than flattened:

* `_SERVICE_DEFS` had two shapes -- a 2-tuple in `backup_manager.py` and a 3-tuple
  elsewhere. All seven keys, env vars and ports were identical and the 2-tuple simply
  lacked the health path, so the 3-tuple is kept as a strict superset. Merging the other
  way would have broken `backup_manager`'s unpack at runtime.
* `populate_services` exists because `zero_downtime_deploy.py` deliberately started with
  an EMPTY registry and filled it through `update_service_url`. A union that always built
  URLs eagerly would have changed its behaviour silently, so it passes False.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The repo root goes on sys.path so the canonical status enum is importable from this
# standalone-script directory -- same bootstrap deployment_rollback.py already uses for
# autobot_shared.paths (#11761).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from autobot_shared.status_enums import HealthStatus  # noqa: E402 - follows the sys.path setup

#: #6973: the canonical ladder, aliased rather than re-declared.
ServiceStatus = HealthStatus


class DeploymentMode(Enum):
    """Deployment modes (mirrors backend utils/service_registry.py)."""

    LOCAL = "local"
    DOCKER_LOCAL = "docker_local"
    DISTRIBUTED = "distributed"
    KUBERNETES = "kubernetes"
    CLOUD = "cloud"




@dataclass
class ServiceHealth:
    """Service health state."""

    status: ServiceStatus
    last_check: float
    response_time: float = 0.0




_SERVICE_DEFS = {
    "backend": ("AUTOBOT_BACKEND_PORT", 8001, "/api/health"),
    "frontend": ("AUTOBOT_FRONTEND_PORT", 5173, "/"),
    "redis": ("AUTOBOT_REDIS_PORT", 6379, None),
    "ollama": ("AUTOBOT_OLLAMA_PORT", 11434, "/api/tags"),
    "ai-stack": ("AUTOBOT_AI_STACK_PORT", 8080, "/health"),
    "npu-worker": ("AUTOBOT_NPU_WORKER_PORT", 8081, "/health"),
    "playwright-vnc": ("AUTOBOT_BROWSER_SERVICE_PORT", 9001, "/health"),
}


class ServiceRegistry:
    """Canonical service registry for the deployment scripts.

    `services` is built eagerly unless `populate_services=False`, which preserves the
    one caller that fills the map itself.
    """

    def __init__(self, config_file: Optional[str] = None, *, populate_services: bool = True) -> None:
        self.config_file = config_file
        self.deployment_mode = self._detect_deployment_mode()
        self.services: Dict[str, str] = (
            {name: self._build_url(name) for name in _SERVICE_DEFS} if populate_services else {}
        )

    @staticmethod
    def _build_url(name: str) -> str:
        """Build a service base URL from environment overrides."""
        host_env = f"AUTOBOT_{name.upper().replace('-', '_')}_HOST"
        host = os.environ.get(host_env, "localhost")
        port_env, default_port, _health = _SERVICE_DEFS[name]
        port = int(os.environ.get(port_env, default_port))
        scheme = "redis" if name == "redis" else "http"
        return f"{scheme}://{host}:{port}"

    @staticmethod
    def _detect_deployment_mode() -> DeploymentMode:
        """Detect deployment mode from env, falling back to container heuristic."""
        mode = os.environ.get("AUTOBOT_DEPLOYMENT_MODE", "").lower()
        if mode:
            try:
                return DeploymentMode(mode)
            except ValueError:
                logger.warning("Invalid AUTOBOT_DEPLOYMENT_MODE: %s", mode)
        return DeploymentMode.DOCKER_LOCAL if os.path.exists("/.dockerenv") else DeploymentMode.LOCAL

    @staticmethod
    def _probe(url: str, timeout: float = 5.0) -> bool:
        """Probe a service: TCP connect for redis, HTTP GET otherwise."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "redis":
            try:
                with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
                    return True
            except OSError:
                return False
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                # bool() because urlopen's response is typed Any under strict mypy.
                return bool(response.status == 200)
        except (urllib.error.URLError, OSError, ValueError):
            return False

    async def check_all_services_health(self) -> Dict[str, ServiceHealth]:
        """Check health of all registered services."""
        names = self.list_services()
        results = await asyncio.gather(*(self.check_service_health(name) for name in names))
        return dict(zip(names, results))

    async def check_service_health(self, service_name: str) -> ServiceHealth:
        """Check health of a single service."""
        _port_env, _default_port, health_path = _SERVICE_DEFS[service_name]
        url = self.get_service_url(service_name, health_path or "")
        start = time.time()
        ok = await asyncio.get_running_loop().run_in_executor(None, self._probe, url)
        return ServiceHealth(
            status=ServiceStatus.HEALTHY if ok else ServiceStatus.UNHEALTHY,
            last_check=time.time(),
            response_time=time.time() - start,
        )

    def get_deployment_info(self) -> Dict[str, Any]:
        """Get deployment information."""
        return {
            "deployment_mode": self.deployment_mode.value,
            "services_count": len(self.services),
            "services": {name: {"url": url} for name, url in self.services.items()},
        }

    def get_service_url(self, service_name: str, path: str = "") -> str:
        """Get full URL for a service."""
        if service_name not in self.services:
            raise ValueError(f"Service '{service_name}' not found in registry")
        return f"{self.services[service_name]}{path}"

    def list_services(self) -> List[str]:
        """List registered service names."""
        return list(self.services)

    def update_service_url(self, name: str, url: str) -> None:
        """Point a service at a new base URL.

        In-process only: this standalone stand-in has no persistent store —
        the canonical registry lives in autobot-backend/utils/service_registry.py.
        """
        self.services[name] = url

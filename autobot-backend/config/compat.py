#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
Playwright/noVNC URL constants.

#18094: the legacy Config wrapper, the manager pass-throughs, the async helpers
and the backend/Ollama/Redis host constants had no caller outside two
backward-compat tests and were removed; the surviving callers read
``autobot_shared.ssot_config`` (``backend_url``, ``ollama_url``). What remains
is the browser-VM noVNC URL: SSOT's ``vnc_url`` points at the main host's
desktop, a different VNC server, so these stay until SSOT has a browser-VM
property.
"""

from config.registry import ConfigRegistry
from constants.network_constants import NetworkConstants

# Playwright/Browser service constants
PLAYWRIGHT_HOST_IP = ConfigRegistry.get("vm.browser", NetworkConstants.BROWSER_VM_IP)
PLAYWRIGHT_VNC_PORT = int(ConfigRegistry.get("port.vnc", str(NetworkConstants.VNC_PORT)))
PLAYWRIGHT_VNC_URL = f"http://{PLAYWRIGHT_HOST_IP}:{PLAYWRIGHT_VNC_PORT}/vnc.html"


# VNC Direct URL function
def get_vnc_direct_url():
    """Get the direct VNC connection URL with appropriate port"""
    return f"http://{PLAYWRIGHT_HOST_IP}:{PLAYWRIGHT_VNC_PORT}/vnc.html"

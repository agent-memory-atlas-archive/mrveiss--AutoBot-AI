# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one home of the environment / deployment-mode / debug settings (#19315, #19316).

``AUTOBOT_ENVIRONMENT`` is the canonical name of the deployment environment. ``AUTOBOT_ENV`` is
the older short spelling of the SAME setting: it is read only when ``AUTOBOT_ENVIRONMENT`` is unset,
and no deployment file writes it. Every reader -- ``ssot_config.AutoBotConfig``, the env registry,
``network_constants``, ``tracing``, the log forwarder -- takes its value or its default from here.

:data:`PROFILES` is the one table of what each deployment profile sets. A file that writes one of
:data:`PROFILE_VARS` (compose, a Dockerfile, an ``.env`` template, an installer) carries exactly one
named profile, and ``repo_tests/deployment_profile_one_source_19315_test.py`` pins its values to
this table. A missing value resolves to :data:`DEFAULT_PROFILE`, which never enables development
behaviour or debug: an unconfigured host runs as production.

Dependency-free on purpose: standalone scripts and pre-init tracing import it without pydantic.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

#: Canonical name of the deployment environment (production / development / test / ...).
ENVIRONMENT_VAR = "AUTOBOT_ENVIRONMENT"
#: Legacy short spelling of the same setting, consulted only when the canonical one is unset.
ENVIRONMENT_ALIAS_VAR = "AUTOBOT_ENV"
#: Topology selector (see ``service_registry_core.DeploymentMode``). Unset is ``""``: auto-detect,
#: decided by ``service_registry_core.resolve_deployment_mode`` (owner ruling on #19315, per #12771).
DEPLOYMENT_MODE_VAR = "AUTOBOT_DEPLOYMENT_MODE"
#: Debug switch; ``"true"``/``"false"`` as written in env files.
DEBUG_VAR = "AUTOBOT_DEBUG"

#: Every variable a profile may set; the guard pins every writer of these names.
PROFILE_VARS = (ENVIRONMENT_VAR, ENVIRONMENT_ALIAS_VAR, DEPLOYMENT_MODE_VAR, DEBUG_VAR)

#: The profile a missing value resolves to.
DEFAULT_PROFILE = "production"

#: profile -> the values it sets. A file carrying a profile may set a subset, never another value.
#: ``hybrid`` and ``native`` are not ``DeploymentMode`` members; their validity is #19187.
PROFILES: dict[str, dict[str, str]] = {
    # What an unset value resolves to. Mode "" = auto-detect (container -> docker_local, else local).
    "production": {ENVIRONMENT_VAR: "production", DEPLOYMENT_MODE_VAR: "", DEBUG_VAR: "false"},
    # The ansible fleet: backend.env.j2 writes the environment explicitly and leaves the mode to detection.
    "fleet": {ENVIRONMENT_VAR: "production"},
    # One host, production: docker-compose.yml and the Dockerfiles (environment only; the container
    # is detected as docker_local) and install-bare-metal.sh (environment and mode).
    "single-host": {ENVIRONMENT_VAR: "production", DEPLOYMENT_MODE_VAR: "local"},
    # Developer checkout: .env.example and docker-compose.override.example.yml.
    "development": {ENVIRONMENT_VAR: "development", DEPLOYMENT_MODE_VAR: "hybrid", DEBUG_VAR: "true"},
    # Development tooling overlays on .env.base (#18555).
    "dev-localhost": {ENVIRONMENT_VAR: "development", DEPLOYMENT_MODE_VAR: "local", DEBUG_VAR: "true"},
    "dev-native-vm": {ENVIRONMENT_VAR: "development", DEPLOYMENT_MODE_VAR: "distributed", DEBUG_VAR: "true"},
    # Topology-only selectors written by the deploy scripts and infrastructure overlays.
    "distributed": {DEPLOYMENT_MODE_VAR: "distributed"},
    "local": {DEPLOYMENT_MODE_VAR: "local"},
    "hybrid": {DEPLOYMENT_MODE_VAR: "hybrid"},
    "native": {DEPLOYMENT_MODE_VAR: "native"},
    "kubernetes": {DEPLOYMENT_MODE_VAR: "kubernetes"},
    "ansible-backend": {DEPLOYMENT_MODE_VAR: "production"},  # not a DeploymentMode member: #19187
}

#: The values a missing setting resolves to.
DEFAULTS: dict[str, str] = PROFILES[DEFAULT_PROFILE]
ENVIRONMENT_DEFAULT = DEFAULTS[ENVIRONMENT_VAR]
DEPLOYMENT_MODE_DEFAULT = DEFAULTS[DEPLOYMENT_MODE_VAR]
DEBUG_DEFAULT = DEFAULTS[DEBUG_VAR] == "true"
#: Names the environment is read from, canonical first (pydantic ``AliasChoices`` order).
ENV_NAMES = (ENVIRONMENT_VAR, ENVIRONMENT_ALIAS_VAR)


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def environment_name(environ: Optional[Mapping[str, str]] = None) -> str:
    """``AUTOBOT_ENVIRONMENT``, else the legacy ``AUTOBOT_ENV``, else the default profile's."""
    env = _env(environ)
    return env.get(ENVIRONMENT_VAR) or env.get(ENVIRONMENT_ALIAS_VAR) or ENVIRONMENT_DEFAULT


def deployment_mode(environ: Optional[Mapping[str, str]] = None) -> str:
    """``AUTOBOT_DEPLOYMENT_MODE``, else the default profile's."""
    return _env(environ).get(DEPLOYMENT_MODE_VAR) or DEPLOYMENT_MODE_DEFAULT

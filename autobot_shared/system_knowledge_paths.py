# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Where the system-knowledge templates live (#18556, #18577).

The ONE tracked tree of templates. The runtime copies the managers write live under
``<project root>/data/system_knowledge`` (gitignored) and are never tracked.
"""

from pathlib import Path

from autobot_shared.paths import project_root

SYSTEM_KNOWLEDGE_TEMPLATES_REL = Path("autobot-backend") / "resources" / "knowledge"


def system_knowledge_templates_dir() -> Path:
    """The system-knowledge template directory under the resolved project root."""
    return project_root() / SYSTEM_KNOWLEDGE_TEMPLATES_REL

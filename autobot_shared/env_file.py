# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one stdlib-only ``.env`` reader (#19232).

Moved out of ``env_drift_detector`` so ``redis_management.database_overrides`` can read the
Redis DB overrides from the project ``.env`` in processes whose venv ships neither
python-dotenv nor pydantic -- the SLM agent (#14278). Stdlib only, on purpose.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

__all__ = ["parse_env_file"]


def parse_env_file(env_path: Path) -> Dict[str, str]:
    """Parse a .env file and return {KEY: value} dict.

    Handles:
    - Blank lines and comment lines (# ...)
    - KEY=value and KEY="value" and KEY='value'
    - Inline comments stripped with care (only unquoted)
    """
    result: Dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("cannot read .env file %s: %s", env_path, exc)
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, raw_val = line.partition("=")
        key = key.strip()
        raw_val = raw_val.strip()

        # Strip surrounding quotes
        if len(raw_val) >= 2 and raw_val[0] in ('"', "'") and raw_val[-1] == raw_val[0]:
            raw_val = raw_val[1:-1]
        else:
            # Strip inline comment (only for unquoted values)
            comment_idx = raw_val.find(" #")
            if comment_idx != -1:
                raw_val = raw_val[:comment_idx].strip()

        if key:
            result[key] = raw_val

    return result

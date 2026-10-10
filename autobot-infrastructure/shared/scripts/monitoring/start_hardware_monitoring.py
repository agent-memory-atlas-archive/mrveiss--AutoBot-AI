#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""
AutoBot hardware monitoring entry point.

Kept so every existing invoker (systemd units, ansible, cron, shell) keeps working. The launcher
logic exists once, in ``start_monitoring.py`` (#17906); this file only selects its hardware backend.
An explicit ``--backend`` on the command line still wins, because it follows the injected default.
"""

import runpy
import sys
from pathlib import Path

_LAUNCHER = Path(__file__).resolve().with_name("start_monitoring.py")


def main() -> None:
    """Run the consolidated launcher with the hardware backend as the default."""
    sys.argv[1:1] = ["--backend", "hardware"]
    runpy.run_path(str(_LAUNCHER), run_name="__main__")


if __name__ == "__main__":
    main()

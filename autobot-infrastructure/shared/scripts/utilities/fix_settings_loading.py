#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""
Settings Loading Fix Utility
Helps diagnose and resolve settings loading issues in AutoBot

Entry point kept for existing invocations; ``SettingsLoadingFixer`` lives in
``settings_diagnostic_utility.py`` (#18964).
"""

from settings_diagnostic_utility import main

if __name__ == "__main__":
    main()

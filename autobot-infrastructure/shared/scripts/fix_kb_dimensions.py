#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""
Fix knowledge base dimension mismatch by recreating with correct settings.

Entry point kept for existing invocations; the implementation lives in
``kb_dimension_utility.py`` (#18964), which reaches Redis through the centralized client.
"""

from kb_dimension_utility import main

if __name__ == "__main__":
    main()

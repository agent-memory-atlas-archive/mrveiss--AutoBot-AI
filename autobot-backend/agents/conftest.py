# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Local pytest configuration for agents tests (#18094).

``agents/npu_code_search_patterns.detect_language`` delegates to the canonical
``code_intelligence.base_analyzer.detect_language``. The top-level conftest stubs
that module (and the ``code_intelligence`` package) as MagicMocks to avoid the
package's heavy ``__init__``; a mock detector would hand every agents test a
``MagicMock`` language. ``base_analyzer`` is a leaf (stdlib plus
``autobot_shared``), so it is real-loaded here, bypassing the package ``__init__``
exactly like ``code_intelligence.shared.scoring`` in the top-level conftest.
"""

from pathlib import Path

from testkit.module_stubs import real_load_and_bind

real_load_and_bind(
    "code_intelligence.base_analyzer",
    Path(__file__).resolve().parent.parent / "code_intelligence" / "base_analyzer.py",
)

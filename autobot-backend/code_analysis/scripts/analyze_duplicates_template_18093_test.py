# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The command-utils template emits the canonical ANSI stripper (#18093).

The template used to carry a private ``strip_ansi_codes`` whose raw-string pattern
double-escaped ``\\x1B`` and so matched the literal text, never the ESC byte.
"""

import ast
from pathlib import Path

_SCRIPT = Path(__file__).with_name("analyze_duplicates.py")


def _template() -> str:
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_COMMAND_UTILS_LIBRARY_CONTENT":
            return ast.literal_eval(node.value)
    raise AssertionError("_COMMAND_UTILS_LIBRARY_CONTENT not found")


def test_template_has_no_private_ansi_pattern():
    text = _template()
    assert "ansi_escape" not in text
    assert "def strip_ansi_codes" not in text
    assert "from utils.encoding_utils import strip_ansi_codes" in text


def test_template_import_target_strips_a_real_esc_byte():
    from utils.encoding_utils import strip_ansi_codes

    real = "\x1b[31mERROR\x1b[0m done"
    assert "\x1b" in real
    assert strip_ansi_codes(real) == "ERROR done"

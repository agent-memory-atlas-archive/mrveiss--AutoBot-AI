#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one word tokenizer for skill routing (#18093).

`prepared_facts._tok` and `skills/builtin/skill_router._tokenize` were byte-identical
copies. The copy existed to dodge a circular import: `skills/__init__` imports the
registry, which imports `prepared_facts`, so `prepared_facts` could not import anything
under `skills`. `autobot_shared` has no such cycle, which is why the helper lives here.
"""

from __future__ import annotations

import re
from typing import FrozenSet

__all__ = ["word_tokens"]

#: A run of anything that is not a letter or digit (underscore counts as a separator).
_SEPARATOR_RUN = re.compile(r"[\W_]+")


def word_tokens(text: str) -> FrozenSet[str]:
    """Lowercase *text* and split it into its word tokens (empty tokens dropped).

    >>> sorted(word_tokens("Analyze_this PDF-document!"))
    ['analyze', 'document', 'pdf', 'this']
    """
    return frozenset(t for t in _SEPARATOR_RUN.split(text.lower()) if t)

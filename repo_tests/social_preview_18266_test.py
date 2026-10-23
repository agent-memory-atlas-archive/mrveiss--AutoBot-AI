# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The GitHub social preview is the render of the current brand mark (#18266, #15469).

`docs/assets/social-preview.png` is produced by `scripts/brand/render_social_preview.mjs`
from `libs/autobot-ui/src/assets/brand/mark.svg`, never redrawn. The pair is pinned like the
favicon .ico (one_brand_mark_and_announcer_18240_test.py): a new mark without a new preview,
or a hand-edited preview, fails here. Re-run the script and update both hashes.
"""

from __future__ import annotations

import hashlib
import struct

from repo_tests._paths import repo_root

_MARK = "libs/autobot-ui/src/assets/brand/mark.svg"
_PREVIEW = "docs/assets/social-preview.png"
_RENDERED_FROM = {
    _MARK: "bf25c31d0e19e610d6c3a8e5ef2d8afa158ecea0ba03dcd2b01c9fb99c526d9c",  # pragma: allowlist secret
    _PREVIEW: "52fa118196c0f14044f4325d809480b5815baa077a9c489a95ce68ced5f9f920",  # pragma: allowlist secret
}


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_the_preview_is_the_size_github_expects() -> None:
    """GitHub's social preview is 1280x640."""
    assert _png_size((repo_root() / _PREVIEW).read_bytes()) == (1280, 640)


def test_the_preview_is_the_render_of_the_current_mark() -> None:
    actual = {path: hashlib.sha256((repo_root() / path).read_bytes()).hexdigest() for path in _RENDERED_FROM}
    assert actual == _RENDERED_FROM, (
        f"#18266: {_MARK} or {_PREVIEW} changed without the other; re-run "
        "scripts/brand/render_social_preview.mjs and update both hashes in _RENDERED_FROM."
    )


def test_the_readme_header_uses_the_canonical_mark() -> None:
    head = (repo_root() / "README.md").read_text(encoding="utf-8")[:600]
    assert f'src="{_MARK}"' in head

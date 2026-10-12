# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18131 -- a kind from each former CaptchaType resolves in the one canonical enum."""

import pytest

from agents.browser.captcha_solver import CaptchaType as BrowserCaptchaType
from autobot_shared.captcha_types import CaptchaType
from services.captcha_solver import CaptchaType as ServicesCaptchaType


def test_both_solver_modules_use_the_canonical_enum() -> None:
    assert BrowserCaptchaType is CaptchaType
    assert ServicesCaptchaType is CaptchaType


@pytest.mark.parametrize("value", ["image_select", "cloudflare"])  # services-only kinds
def test_services_only_kinds_resolve(value: str) -> None:
    assert CaptchaType(value).value == value


def test_browser_only_slider_resolves() -> None:
    assert CaptchaType("slider") is CaptchaType.SLIDER


def test_text_image_legacy_value_maps_to_text() -> None:
    assert CaptchaType("text_image") is CaptchaType.TEXT

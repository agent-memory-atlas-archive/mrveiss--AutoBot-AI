# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one CAPTCHA taxonomy, shared by the services and browser solvers (#18131)."""

from enum import Enum


class CaptchaType(Enum):
    """The one CAPTCHA taxonomy (#18131): union of both former solvers' kinds.

    ``TEXT`` is the single spelling for distorted-text images; the recorded legacy
    wire value ``"text_image"`` resolves to it via ``_missing_``.
    """

    TEXT = "text"  # Distorted text rendered as an image
    MATH = "math"  # Math equation to solve
    IMAGE_SELECT = "image_select"  # Select specific images
    SLIDER = "slider"  # Drag-slider challenge
    RECAPTCHA = "recaptcha"  # Google reCAPTCHA
    HCAPTCHA = "hcaptcha"  # hCaptcha
    CLOUDFLARE = "cloudflare"  # Cloudflare challenge
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> "CaptchaType | None":
        if value == "text_image":
            return cls.TEXT
        return None

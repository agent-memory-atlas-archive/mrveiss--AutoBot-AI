// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * HTML sanitization for the SLM frontend (#19118). Same DOMPurify approach
 * as autobot-frontend/src/utils/sanitize.ts (#2847); every v-html binding in
 * this app MUST pass its value through sanitizeHtml.
 */

import DOMPurify from 'dompurify'

const FORMATTING_ONLY = {
  ALLOWED_TAGS: ['br', 'strong', 'em', 'code', 'span'],
  ALLOWED_ATTR: ['class'],
}

/** Strip everything but basic inline formatting; safe for v-html. */
export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, FORMATTING_ONLY) as string
}

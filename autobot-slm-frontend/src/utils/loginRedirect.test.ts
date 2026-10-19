// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

import { describe, it, expect, afterEach, vi } from 'vitest'
import { buildLoginUrl, isLoginPathname, loginPath, safeRedirectTarget } from './loginRedirect'

describe('loginRedirect (#18892)', () => {
  afterEach(() => vi.unstubAllEnvs())

  it('builds the login path under the /slm/ base', () => {
    vi.stubEnv('BASE_URL', '/slm/')
    expect(loginPath()).toBe('/slm/login')
    expect(isLoginPathname('/slm/login')).toBe(true)
    expect(isLoginPathname('/login')).toBe(false)
  })

  it('works standalone at the root base', () => {
    vi.stubEnv('BASE_URL', '/')
    expect(loginPath()).toBe('/login')
    expect(buildLoginUrl({ pathname: '/fleet', search: '', hash: '' })).toBe(
      '/login?redirect=%2Ffleet'
    )
  })

  it('strips the base from the redirect value', () => {
    vi.stubEnv('BASE_URL', '/slm/')
    expect(buildLoginUrl({ pathname: '/slm/nodes/1', search: '?a=b', hash: '#c' })).toBe(
      `/slm/login?redirect=${encodeURIComponent('/nodes/1?a=b#c')}`
    )
  })

  it('only follows same-app redirect targets', () => {
    expect(safeRedirectTarget('/fleet')).toBe('/fleet')
    expect(safeRedirectTarget('//evil.example')).toBe('/')
    expect(safeRedirectTarget('https://evil.example')).toBe('/')
    expect(safeRedirectTarget(undefined)).toBe('/')
  })
})

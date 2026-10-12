// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * SSOCallbackView - the landing exchanges a one-time code, never reads a
 * token from the URL, and strips the query from history (#18394).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { push, query, rawRequest, checkAuth } = vi.hoisted(() => ({
  push: vi.fn(),
  query: {} as Record<string, string>,
  rawRequest: vi.fn(),
  checkAuth: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ query }),
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ checkAuth }) }))
vi.mock('@/utils/ApiClient', () => ({ slmApiClient: { rawRequest } }))

import SSOCallbackView from './SSOCallbackView.vue'

function mountView() {
  return mount(SSOCallbackView, { global: { mocks: { $t: (k: string) => k } } })
}

describe('SSOCallbackView (#18394)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    checkAuth.mockResolvedValue(true)
    for (const k of Object.keys(query)) delete query[k]
    sessionStorage.clear()
    window.history.replaceState(null, '', '/sso-callback?code=abc')
  })

  it('exchanges the code by POST, stores the token, strips the query', async () => {
    query.code = 'abc'
    rawRequest.mockResolvedValue({ ok: true, json: async () => ({ access_token: 'jwt' }) })
    mountView()
    await flushPromises()
    expect(rawRequest).toHaveBeenCalledWith('/auth/sso/exchange', { method: 'POST', body: { code: 'abc' } })
    expect(sessionStorage.getItem('slm_access_token')).toBe('jwt')
    expect(window.location.search).toBe('')
    expect(push).toHaveBeenCalledWith('/')
  })

  it('ignores a legacy ?token= param and goes to login', async () => {
    query.token = 'leaked'
    window.history.replaceState(null, '', '/sso-callback?token=leaked')
    mountView()
    await flushPromises()
    expect(rawRequest).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('slm_access_token')).toBeNull()
    expect(window.location.search).toBe('')
    expect(push).toHaveBeenCalledWith({ name: 'login', query: { error: 'no_token' } })
  })

  it('sends a rejected code to login with sso_failed', async () => {
    query.code = 'used'
    rawRequest.mockResolvedValue({ ok: false })
    mountView()
    await flushPromises()
    expect(sessionStorage.getItem('slm_access_token')).toBeNull()
    expect(push).toHaveBeenCalledWith({ name: 'login', query: { error: 'sso_failed' } })
  })
})

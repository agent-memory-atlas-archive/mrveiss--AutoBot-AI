// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
/** ProviderOAuthConnect (#19241): server-driven sign-in, no endpoints in the SPA. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/locales/en.json'

const post = vi.fn()
const get = vi.fn()
vi.mock('@/utils/ApiClient', () => ({
  default: { post: (...a: unknown[]) => post(...a), get: (...a: unknown[]) => get(...a), delete: vi.fn() },
}))
vi.mock('@/config/ssot-config', () => ({
  getConfig: () => ({ frontendUrl: 'https://app.example.test' }),
}))

import ProviderOAuthConnect from './ProviderOAuthConnect.vue'
import { PROVIDER_OAUTH_CALLBACK_PATH } from '@/utils/providerOAuth'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const mountIt = (props: Record<string, unknown>) =>
  mount(ProviderOAuthConnect, {
    props: { providerName: 'openai', providerLabel: 'OpenAI', scope: 'user', ...props },
    global: { plugins: [i18n] },
  })

beforeEach(() => {
  post.mockReset()
  get.mockReset()
  get.mockResolvedValue({ provider_name: 'openai', connected: false })
})

describe('ProviderOAuthConnect', () => {
  it('initiate posts provider/redirect_uri/scope and navigates to authorize_url', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    post.mockResolvedValue({ authorize_url: 'https://idp.example.test/authorize?x=1', state: 's' })
    const wrapper = mountIt({ flows: ['redirect'], configured: true })
    await flushPromises()
    await wrapper.get('[data-testid="provider-signin"]').trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/api/llm-auth/oauth/initiate', {
      provider_name: 'openai',
      redirect_uri: `https://app.example.test${PROVIDER_OAUTH_CALLBACK_PATH}`,
      scope: 'user',
    })
    expect(assign).toHaveBeenCalledWith('https://idp.example.test/authorize?x=1')
    vi.unstubAllGlobals()
  })

  it('unconfigured provider shows setting names and no sign-in button', async () => {
    const wrapper = mountIt({
      configured: false,
      flows: [],
      missingSettings: ['AUTOBOT_LLM_OAUTH_OPENAI_CLIENT_ID'],
    })
    await flushPromises()
    const box = wrapper.get('[data-testid="provider-not-configured"]')
    expect(box.text()).toContain('AUTOBOT_LLM_OAUTH_OPENAI_CLIENT_ID')
    expect(wrapper.find('[data-testid="provider-signin"]').exists()).toBe(false)
  })
})

// Copyright 2025-2026 mrveiss
// SPDX-License-Identifier: Apache-2.0
// AutoBot - AI-Powered Automation Platform
// Author: mrveiss

/**
 * #19270 — the node lifecycle panel never shows invented events. A failed fetch used to
 * render 15 random lifecycle events as the node's history; now it says the events could not
 * be loaded and offers a retry.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { getNodeEvents } = vi.hoisted(() => ({ getNodeEvents: vi.fn() }))
vi.mock('@/composables/useSlmApi', () => ({ useSlmApi: () => ({ getNodeEvents, applyUpdates: vi.fn() }) }))
vi.mock('@/stores/fleet', () => ({ useFleetStore: () => ({ checkNodeUpdates: vi.fn() }) }))

import NodeLifecyclePanel from '../NodeLifecyclePanel.vue'

class FakeSocket {
  onopen: unknown = null
  onmessage: unknown = null
  onerror: unknown = null
  onclose: unknown = null
  close(): void {}
}

function mountPanel() {
  return mount(NodeLifecyclePanel, {
    props: { nodeId: 'node-1', autoRefreshInterval: 3_600_000 },
    global: { mocks: { $t: (key: string) => key } },
  })
}

beforeEach(() => {
  getNodeEvents.mockReset()
  vi.stubGlobal('WebSocket', FakeSocket)
})

describe('NodeLifecyclePanel (#19270)', () => {
  it('a failed fetch says the events could not be loaded and shows no event rows', async () => {
    getNodeEvents.mockRejectedValue(new Error('HTTP 502'))
    const wrapper = mountPanel()
    await flushPromises()
    const alert = wrapper.get('[data-testid="lifecycle-load-error"]')
    expect(alert.text()).toContain('fleet.nodeLifecyclePanel.eventsUnavailable')
    expect(alert.text()).toContain('HTTP 502')
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
  })

  it('retry fetches again and clears the error once events load', async () => {
    getNodeEvents.mockRejectedValueOnce(new Error('HTTP 502')).mockResolvedValue([])
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-testid="lifecycle-load-error"] button').trigger('click')
    await flushPromises()
    expect(getNodeEvents).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="lifecycle-load-error"]').exists()).toBe(false)
  })

  it('an empty successful read is the empty state, not the error', async () => {
    getNodeEvents.mockResolvedValue([])
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="lifecycle-load-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('fleet.nodeLifecyclePanel.noLifecycleEventsRecordedYet')
  })
})
